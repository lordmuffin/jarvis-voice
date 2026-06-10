package com.lordmuffin.jarvisvoice

import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.content.pm.ServiceInfo
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.work.*
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File
import java.io.FileOutputStream
import java.net.HttpURLConnection
import java.net.URL

class ModelDownloadWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {

    companion object {
        const val KEY_MODEL_ID  = "model_id"
        const val KEY_PROGRESS  = "progress"
        const val KEY_ERROR     = "error"
        const val CHANNEL_ID    = "jarvis_model_download"
        const val NOTIF_BASE_ID = 200

        fun workName(modelId: String) = "llm_download_$modelId"

        fun enqueue(context: Context, modelId: String) {
            val req = OneTimeWorkRequestBuilder<ModelDownloadWorker>()
                .setInputData(workDataOf(KEY_MODEL_ID to modelId))
                .setConstraints(
                    Constraints.Builder()
                        .setRequiredNetworkType(NetworkType.CONNECTED)
                        .build()
                )
                .build()
            // REPLACE: if a previous download is stuck/enqueued, cancel it and start fresh.
            WorkManager.getInstance(context)
                .enqueueUniqueWork(workName(modelId), ExistingWorkPolicy.REPLACE, req)
        }

        fun cancel(context: Context, modelId: String) {
            WorkManager.getInstance(context).cancelUniqueWork(workName(modelId))
        }
    }

    private val nm     get() = applicationContext.getSystemService(NotificationManager::class.java)
    private val notifId get() = NOTIF_BASE_ID + (inputData.getString(KEY_MODEL_ID)?.hashCode() ?: 0)

    override suspend fun getForegroundInfo(): ForegroundInfo {
        createChannel()
        val notif = buildNotif("Starting…", 0, true)
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            ForegroundInfo(notifId, notif, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        } else {
            ForegroundInfo(notifId, notif)
        }
    }

    override suspend fun doWork(): Result {
        val modelId = inputData.getString(KEY_MODEL_ID)
            ?: return Result.failure(workDataOf(KEY_ERROR to "no model_id"))
        val config  = ModelRegistry.MODELS.find { it.id == modelId }
            ?: return Result.failure(workDataOf(KEY_ERROR to "unknown model: $modelId"))

        setForeground(getForegroundInfo())

        val mgr      = LlmModelManager(applicationContext)
        val destFile = mgr.modelFile(config)
        val tmpFile  = File(destFile.parent, "${config.filename}.tmp")

        DebugLog.i("ModelDownload", "starting $modelId → ${config.downloadUrl}")

        return try {
            val result = withContext(Dispatchers.IO) {
                downloadFile(config, tmpFile)
            }

            when {
                isStopped      -> { tmpFile.delete(); nm.cancel(notifId); Result.failure() }
                result == null -> {
                    tmpFile.renameTo(destFile)
                    nm.cancel(notifId)
                    DebugLog.i("ModelDownload", "$modelId complete: ${destFile.length()} bytes")
                    Result.success()
                }
                else           -> {
                    tmpFile.delete()
                    DebugLog.e("ModelDownload", "download failed for $modelId: $result")
                    showErrorNotification(config.displayName, result)
                    nm.cancel(notifId)
                    Result.failure(workDataOf(KEY_ERROR to result))
                }
            }
        } catch (e: Exception) {
            DebugLog.e("ModelDownload", "download failed for $modelId", e)
            tmpFile.delete()
            nm.cancel(notifId)
            Result.failure(workDataOf(KEY_ERROR to (e.message ?: "unknown error")))
        }
    }

    /** Returns null on success, error string on failure. Runs on IO dispatcher. */
    private suspend fun downloadFile(config: ModelConfig, tmpFile: File): String? {
        val conn = URL(config.downloadUrl).openConnection() as HttpURLConnection
        conn.connectTimeout = 15_000
        conn.readTimeout    = 60_000
        conn.connect()

        if (conn.responseCode !in 200..299) return "HTTP ${conn.responseCode}"

        val total      = conn.contentLengthLong
        var downloaded = 0L

        val input  = conn.inputStream
        val output = FileOutputStream(tmpFile)

        try {
            val buf = ByteArray(65_536)
            var n = 0
            while (!isStopped && input.read(buf).also { n = it } != -1) {
                output.write(buf, 0, n)
                downloaded += n
                val pct = if (total > 0) (downloaded * 100 / total).toInt() else 0
                setProgress(workDataOf(KEY_PROGRESS to pct))
                nm.notify(notifId, buildNotif(config.displayName, pct, false))
            }
        } finally {
            output.flush()
            output.close()
            input.close()
        }

        return if (isStopped) "cancelled" else null
    }

    private fun showErrorNotification(modelName: String, error: String) {
        createChannel()
        val notif = NotificationCompat.Builder(applicationContext, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.stat_notify_error)
            .setContentTitle("Download failed: $modelName")
            .setContentText(error)
            .setAutoCancel(true)
            .build()
        nm.notify(notifId + 1, notif)
    }

    private fun createChannel() {
        nm.createNotificationChannel(
            NotificationChannel(CHANNEL_ID, "Model Downloads", NotificationManager.IMPORTANCE_LOW)
                .also { it.description = "Gemma model download progress" }
        )
    }

    private fun buildNotif(label: String, progress: Int, indeterminate: Boolean) =
        NotificationCompat.Builder(applicationContext, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.stat_sys_download)
            .setContentTitle("Downloading $label")
            .setContentText(if (indeterminate) "Starting…" else "$progress%")
            .setProgress(100, progress, indeterminate)
            .setOngoing(true)
            .setSilent(true)
            .build()
}
