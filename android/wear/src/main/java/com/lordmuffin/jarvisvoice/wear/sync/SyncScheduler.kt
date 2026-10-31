package com.lordmuffin.jarvisvoice.wear.sync

import android.content.Context
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import com.lordmuffin.jarvisvoice.wear.RecorderState
import com.lordmuffin.jarvisvoice.wear.WearGraph
import com.lordmuffin.jarvisvoice.wearcommon.sync.RecordingCatalog
import java.util.concurrent.TimeUnit

/** Runs the sync pass at the right moments: after a recording, on an ack, when a phone or network appears, when a backoff ends. */
object SyncScheduler {
    private const val NOW = "wear-sync-now"
    private const val LATER = "wear-sync-later"
    private const val ONLINE = "wear-sync-online"

    fun kick(ctx: Context) {
        val req = OneTimeWorkRequestBuilder<SyncWorker>().build()
        WorkManager.getInstance(ctx).enqueueUniqueWork(NOW, ExistingWorkPolicy.REPLACE, req)
    }

    fun later(ctx: Context, delayMs: Long) {
        val req = OneTimeWorkRequestBuilder<SyncWorker>()
            .setInitialDelay(delayMs.coerceAtLeast(1_000), TimeUnit.MILLISECONDS).build()
        WorkManager.getInstance(ctx).enqueueUniqueWork(LATER, ExistingWorkPolicy.REPLACE, req)
    }

    /** Wake when the watch gets internet (Wi-Fi in range, LTE, or the phone's connection). */
    fun whenOnline(ctx: Context) {
        val req = OneTimeWorkRequestBuilder<SyncWorker>()
            .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()).build()
        WorkManager.getInstance(ctx).enqueueUniqueWork(ONLINE, ExistingWorkPolicy.KEEP, req)
    }
}

class SyncWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {
    override suspend fun doWork(): Result {
        val graph = WearGraph.get(applicationContext)
        return try {
            // Audio on disk the queue forgot (killed mid-recording, lost queue file) is queued first.
            RecordingCatalog.recoverOrphans(graph.store, graph.recordingsDir, RecorderState::isActive, System.currentTimeMillis())
            DataLayerPull.pull(applicationContext, graph)   // acks and config that arrived while we weren't listening
            val report = graph.runner.runOnce()
            report.wakeAtMs?.let { SyncScheduler.later(applicationContext, it - System.currentTimeMillis()) }
            if (report.waitingForConnectivity) SyncScheduler.whenOnline(applicationContext)
            Result.success()
        } catch (e: kotlinx.coroutines.CancellationException) {
            throw e
        } catch (e: Exception) {
            Result.retry()
        }
    }
}
