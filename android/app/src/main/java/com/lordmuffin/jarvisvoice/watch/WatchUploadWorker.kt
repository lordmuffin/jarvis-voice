package com.lordmuffin.jarvisvoice.watch

import android.content.Context
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import com.lordmuffin.jarvisvoice.DebugLog
import com.lordmuffin.jarvisvoice.live.LiveSettings
import com.lordmuffin.jarvisvoice.wearcommon.inbox.WatchInbox
import com.lordmuffin.jarvisvoice.wearcommon.inbox.WatchUploadProcessor
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.TimeUnit

/**
 * Sends recordings that arrived from the watch to the server through the phone's own client, then
 * tells the watch. The watch deletes its copy only on that message, which is sent only after the
 * server's 2xx. Retries with backoff while the network is down.
 */
class WatchUploadWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {

    override suspend fun doWork(): Result {
        val settings = LiveSettings(applicationContext)
        if (!settings.isConfigured) {
            DebugLog.i(TAG, "not signed in; ${inbox(applicationContext).pending().size} watch recording(s) wait")
            return Result.failure()   // enqueued again when a token is saved (WearBridge) or the app opens
        }
        val uploader = settings.apiClient().recordingUploader()
        val processor = WatchUploadProcessor(
            inbox(applicationContext),
            upload = { entry ->
                val title = "Watch memo " + SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.US).format(Date(entry.meta.startedAtMs))
                uploader.upload(entry.file, entry.meta.id, title, source = "watch")
            },
            ack = { id, status -> WearBridge.publishAck(applicationContext, id, status) },
        )
        return try {
            when (processor.processAll()) {
                WatchUploadProcessor.Outcome.DONE -> Result.success()
                WatchUploadProcessor.Outcome.RETRY_LATER -> Result.retry()
                WatchUploadProcessor.Outcome.AUTH_BLOCKED -> Result.failure()
            }
        } catch (e: kotlinx.coroutines.CancellationException) {
            throw e
        } catch (e: Exception) {
            DebugLog.e(TAG, "watch upload pass failed: ${e.message}")
            Result.retry()
        }
    }

    companion object {
        private const val TAG = "WatchUpload"
        private const val WORK = "watch-upload"

        fun inbox(ctx: Context) = WatchInbox(File(ctx.filesDir, "watch_inbox"))

        fun enqueue(ctx: Context) {
            val req = OneTimeWorkRequestBuilder<WatchUploadWorker>()
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS)
                .build()
            WorkManager.getInstance(ctx).enqueueUniqueWork(WORK, ExistingWorkPolicy.KEEP, req)
        }

        /** App start: pick up recordings that arrived while signed out or offline, and refresh the watch's config. */
        fun resume(ctx: Context) {
            if (inbox(ctx).pending().isNotEmpty()) enqueue(ctx)
            WearBridge.publishConfigAsync(ctx)
        }
    }
}
