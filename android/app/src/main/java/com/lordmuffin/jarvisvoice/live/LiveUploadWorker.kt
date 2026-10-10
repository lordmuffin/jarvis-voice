package com.lordmuffin.jarvisvoice.live

import android.content.Context
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import com.lordmuffin.jarvisvoice.DebugLog
import com.lordmuffin.jarvisvoice.live.protocol.Channel
import java.io.IOException
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.withTimeoutOrNull

/**
 * Gets a stopped session's audio to the server — the Mac's "Upload" menu item, run in the
 * background. A local-only session first gets a server session (its outbox is renamed to the
 * server id); then the outbox is replayed over the normal stream protocol and ended, and the
 * server transcribes it. Also resumes online sessions that were interrupted (network loss at stop,
 * process death mid-session).
 */
class LiveUploader(private val context: Context) {
    private val settings = LiveSettings(context)
    private val repo = SessionRepository(settings.sessionsRoot)

    sealed class Outcome {
        data class Done(val serverId: String) : Outcome()
        data class Failed(val reason: String) : Outcome()
        /** Network trouble; try again later. */
        data class Retry(val reason: String) : Outcome()
    }

    suspend fun upload(sessionId: String): Outcome {
        var meta = repo.read(sessionId) ?: return Outcome.Failed("session not found")
        if (meta.state == SessionMeta.State.DONE) return Outcome.Done(meta.serverId ?: meta.id)
        if (!settings.isConfigured) return Outcome.Failed("Jarvis Live device token not set")
        val api = settings.apiClient()

        try {
            if (meta.serverId == null) {
                val serverId = api.createSession(meta.mode, listOf(Channel.MIC), meta.title)
                meta = repo.rename(meta, serverId).copy(serverId = serverId, state = SessionMeta.State.PENDING)
                repo.write(meta)
            }
        } catch (e: LiveHttpException) {
            return if (e.isFatal) fail(meta, e.message!!) else Outcome.Retry(e.message!!)
        } catch (e: IOException) {
            return Outcome.Retry(e.message ?: "network error")
        }

        val outbox = Outbox.reopen(settings.sessionsRoot, meta.id)
        val terminal = try {
            coroutineScope {
                val connection = LiveConnection(api, meta.id, settings.deviceName, outbox, this)
                // Stay inside WorkManager's 10-minute window; unfinished work resumes on retry.
                withTimeoutOrNull(UPLOAD_TIMEOUT_MS) { connection.end() }
                    ?: ConnState.Idle.also { connection.stop() }
            }
        } finally {
            outbox.close()
        }
        return when (terminal) {
            is ConnState.Ended -> {
                repo.write(meta.copy(state = SessionMeta.State.DONE, error = null))
                meta.notePath?.let { LocalMarkdown.markUploaded(context, it, meta.id) }
                DebugLog.i(TAG, "uploaded ${meta.id}")
                Outcome.Done(meta.id)
            }
            is ConnState.Failed -> fail(meta, terminal.reason)
            else -> Outcome.Retry("connection stopped")
        }
    }

    private fun fail(meta: SessionMeta, reason: String): Outcome.Failed {
        DebugLog.e(TAG, "upload ${meta.id} failed: $reason")
        repo.write(meta.copy(state = SessionMeta.State.FAILED, error = reason))
        return Outcome.Failed(reason)
    }

    private companion object {
        const val TAG = "LiveUpload"
        const val UPLOAD_TIMEOUT_MS = 8 * 60_000L
    }
}

class LiveUploadWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {

    override suspend fun doWork(): Result {
        val id = inputData.getString(KEY_SESSION) ?: return Result.failure()
        return when (val outcome = LiveUploader(applicationContext).upload(id)) {
            is LiveUploader.Outcome.Done -> Result.success()
            is LiveUploader.Outcome.Failed -> Result.failure(workDataOf(KEY_ERROR to outcome.reason))
            is LiveUploader.Outcome.Retry -> {
                DebugLog.i("LiveUpload", "retry $id: ${outcome.reason}")
                Result.retry()
            }
        }
    }

    companion object {
        const val TAG_UPLOAD = "jarvis_live_upload"
        private const val KEY_SESSION = "session_id"
        private const val KEY_ERROR = "error"

        fun workName(sessionId: String) = "live-upload-$sessionId"

        fun enqueue(context: Context, sessionId: String) {
            val req = OneTimeWorkRequestBuilder<LiveUploadWorker>()
                .setInputData(workDataOf(KEY_SESSION to sessionId))
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS)
                .addTag(TAG_UPLOAD)
                .build()
            WorkManager.getInstance(context)
                .enqueueUniqueWork(workName(sessionId), ExistingWorkPolicy.KEEP, req)
        }

        /** Resumes online sessions that never finished (local-only ones wait for the user). */
        fun enqueueInterrupted(context: Context, activeId: String?) {
            val repo = SessionRepository(LiveSettings(context).sessionsRoot)
            for (meta in repo.pending(activeId)) {
                if (meta.serverId != null && meta.state != SessionMeta.State.FAILED) enqueue(context, meta.id)
            }
        }
    }
}
