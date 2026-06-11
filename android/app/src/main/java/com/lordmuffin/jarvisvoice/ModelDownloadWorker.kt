package com.lordmuffin.jarvisvoice

import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.content.pm.ServiceInfo
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.work.*
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.ConnectionPool
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File
import java.io.FileOutputStream
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicLong

class ModelDownloadWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {

    companion object {
        const val KEY_MODEL_ID  = "model_id"
        const val KEY_PROGRESS  = "progress"
        const val KEY_SPEED_BPS = "speed_bps"
        const val KEY_ETA_SEC   = "eta_sec"
        const val KEY_ERROR     = "error"
        const val CHANNEL_ID    = "jarvis_model_download"
        const val NOTIF_BASE_ID = 200

        private const val PARALLEL_CHUNKS = 16
        private const val BUFFER_SIZE     = 256 * 1024        // 256 KB
        private const val PARALLEL_MIN    = 10 * 1024 * 1024L  // only parallel for files > 10 MB

        // Shared client — connection pool sized for PARALLEL_CHUNKS + headroom.
        // OkHttp negotiates HTTP/2 via ALPN when the CDN supports it (Cloudflare does),
        // which reduces per-connection overhead vs raw HttpURLConnection.
        private val httpClient = OkHttpClient.Builder()
            .connectionPool(ConnectionPool(PARALLEL_CHUNKS + 4, 5, TimeUnit.MINUTES))
            .connectTimeout(15, TimeUnit.SECONDS)
            .readTimeout(60, TimeUnit.SECONDS)
            .followRedirects(true)
            .build()

        fun workName(modelId: String) = "llm_download_$modelId"

        fun enqueue(context: Context, modelId: String) {
            val req = OneTimeWorkRequestBuilder<ModelDownloadWorker>()
                .setInputData(workDataOf(KEY_MODEL_ID to modelId))
                .addTag(JarvisApp.TAG_DOWNLOAD)
                .setConstraints(
                    Constraints.Builder()
                        .setRequiredNetworkType(NetworkType.CONNECTED)
                        .build()
                )
                .build()
            WorkManager.getInstance(context)
                .enqueueUniqueWork(workName(modelId), ExistingWorkPolicy.REPLACE, req)
        }

        fun cancel(context: Context, modelId: String) {
            WorkManager.getInstance(context).cancelUniqueWork(workName(modelId))
        }
    }

    private val nm      get() = applicationContext.getSystemService(NotificationManager::class.java)
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
            val error = downloadFile(config, tmpFile)

            when {
                isStopped    -> { cleanupParts(config, tmpFile); Result.failure() }
                error == null -> {
                    tmpFile.renameTo(destFile)
                    nm.cancel(notifId)
                    DebugLog.i("ModelDownload", "$modelId complete: ${destFile.length()} bytes")
                    Result.success()
                }
                else          -> {
                    tmpFile.delete()
                    DebugLog.e("ModelDownload", "failed for $modelId: $error")
                    showErrorNotification(config.displayName, error)
                    nm.cancel(notifId)
                    Result.failure(workDataOf(KEY_ERROR to error))
                }
            }
        } catch (e: Exception) {
            DebugLog.e("ModelDownload", "failed for $modelId", e)
            tmpFile.delete()
            nm.cancel(notifId)
            Result.failure(workDataOf(KEY_ERROR to (e.message ?: "unknown error")))
        }
    }

    private suspend fun downloadFile(config: ModelConfig, tmpFile: File): String? {
        val (url, authHeader) = ModelServerConfig.resolveLlm(applicationContext, config)
        DebugLog.i("ModelDownload", "source=${if (ModelServerConfig.isCustomLlmConfigured(applicationContext)) "custom" else "hf"} url=$url")

        val probe = probeSize(url, authHeader)
        return if (probe != null && probe.totalSize >= PARALLEL_MIN) {
            DebugLog.i("ModelDownload", "parallel: $PARALLEL_CHUNKS streams, ${probe.totalSize / 1_048_576} MB")
            downloadParallel(config, tmpFile, authHeader, probe.resolvedUrl, probe.totalSize)
        } else {
            DebugLog.i("ModelDownload", "single-stream fallback (probe: $probe)")
            downloadSingleStream(config, tmpFile, authHeader, url)
        }
    }

    // ── Parallel chunked download ─────────────────────────────────────────────

    private suspend fun downloadParallel(
        config: ModelConfig,
        tmpFile: File,
        authHeader: String?,
        resolvedUrl: String,
        totalSize: Long,
    ): String? = coroutineScope {

        val chunkSize = (totalSize + PARALLEL_CHUNKS - 1) / PARALLEL_CHUNKS
        val partFiles = (0 until PARALLEL_CHUNKS).map { i ->
            File(tmpFile.parent, "${config.filename}.part$i")
        }

        val totalDownloaded = AtomicLong(partFiles.sumOf { if (it.exists()) it.length() else 0L })

        data class Sample(val bytes: Long, val ms: Long)
        val speedWin = ArrayDeque<Sample>()

        val ticker = launch {
            while (isActive) {
                val now = System.currentTimeMillis()
                val dl  = totalDownloaded.get()
                val pct = (dl * 100 / totalSize).toInt().coerceIn(0, 100)
                speedWin.addLast(Sample(dl, now))
                while (speedWin.size > 1 && now - speedWin.first().ms > 3_000L) speedWin.removeFirst()
                val winMs    = maxOf(1L, speedWin.last().ms - speedWin.first().ms)
                val speedBps = if (speedWin.size > 1)
                    (speedWin.last().bytes - speedWin.first().bytes) * 1000L / winMs else 0L
                val etaSec   = if (speedBps > 0) (totalSize - dl) / speedBps else -1L
                setProgress(workDataOf(KEY_PROGRESS to pct, KEY_SPEED_BPS to speedBps, KEY_ETA_SEC to etaSec))
                nm.notify(notifId, buildNotif(config.displayName, pct, false, speedBps, etaSec))
                delay(500)
            }
        }

        val chunkErrors = (0 until PARALLEL_CHUNKS).map { i ->
            async(Dispatchers.IO) {
                val rangeStart = i * chunkSize
                val rangeEnd   = minOf(rangeStart + chunkSize - 1, totalSize - 1)
                val partFile   = partFiles[i]
                val existing   = if (partFile.exists()) partFile.length() else 0L
                downloadChunk(resolvedUrl, authHeader, rangeStart + existing, rangeEnd, partFile, existing > 0, totalDownloaded)
            }
        }.map { it.await() }

        ticker.cancel()

        val error = chunkErrors.firstOrNull { it != null }
        if (error != null) {
            partFiles.forEach { it.delete() }
            return@coroutineScope error
        }

        withContext(Dispatchers.IO) {
            FileOutputStream(tmpFile).use { out ->
                partFiles.forEach { part ->
                    part.inputStream().use { it.copyTo(out, BUFFER_SIZE) }
                    part.delete()
                }
            }
        }

        null
    }

    private fun downloadChunk(
        url: String,
        authHeader: String?,
        rangeStart: Long,
        rangeEnd: Long,
        partFile: File,
        append: Boolean,
        totalDownloaded: AtomicLong,
    ): String? {
        if (rangeStart > rangeEnd) return null

        val request = Request.Builder()
            .url(url)
            .addHeader("Range", "bytes=$rangeStart-$rangeEnd")
            .apply { if (!authHeader.isNullOrBlank()) addHeader("Authorization", authHeader) }
            .build()

        return try {
            httpClient.newCall(request).execute().use { response ->
                if (!response.isSuccessful) return "HTTP ${response.code} (chunk)"

                FileOutputStream(partFile, append).use { out ->
                    val buf = ByteArray(BUFFER_SIZE)
                    response.body!!.byteStream().use { input ->
                        var n = 0
                        while (!isStopped && input.read(buf).also { n = it } != -1) {
                            out.write(buf, 0, n)
                            totalDownloaded.addAndGet(n.toLong())
                        }
                    }
                    out.flush()
                }

                if (isStopped) "cancelled" else null
            }
        } catch (e: Exception) {
            "chunk error: ${e.message}"
        }
    }

    // ── Single-stream fallback ────────────────────────────────────────────────

    private suspend fun downloadSingleStream(
        config: ModelConfig,
        tmpFile: File,
        authHeader: String?,
        url: String,
    ): String? = withContext(Dispatchers.IO) {
        val request = Request.Builder()
            .url(url)
            .apply { if (!authHeader.isNullOrBlank()) addHeader("Authorization", authHeader) }
            .build()

        httpClient.newCall(request).execute().use { response ->
            when (response.code) {
                401  -> return@withContext "HTTP 401 — check your auth token in Settings"
                403  -> return@withContext "HTTP 403 — access denied (check token or bucket permissions)"
            }
            if (!response.isSuccessful) return@withContext "HTTP ${response.code}"

            val total      = response.header("Content-Length")?.toLongOrNull() ?: -1L
            var downloaded = 0L

            data class Sample(val bytes: Long, val ms: Long)
            val speedWin = ArrayDeque<Sample>()

            FileOutputStream(tmpFile).use { out ->
                val buf = ByteArray(BUFFER_SIZE)
                response.body!!.byteStream().use { input ->
                    var n = 0
                    while (!isStopped && input.read(buf).also { n = it } != -1) {
                        out.write(buf, 0, n)
                        downloaded += n
                        val pct   = if (total > 0) (downloaded * 100 / total).toInt() else 0
                        val nowMs = System.currentTimeMillis()
                        speedWin.addLast(Sample(downloaded, nowMs))
                        while (speedWin.size > 1 && nowMs - speedWin.first().ms > 3_000L) speedWin.removeFirst()
                        val winMs    = maxOf(1L, speedWin.last().ms - speedWin.first().ms)
                        val speedBps = (speedWin.last().bytes - speedWin.first().bytes) * 1000L / winMs
                        val etaSec   = if (speedBps > 0 && total > 0) (total - downloaded) / speedBps else -1L
                        setProgress(workDataOf(KEY_PROGRESS to pct, KEY_SPEED_BPS to speedBps, KEY_ETA_SEC to etaSec))
                        nm.notify(notifId, buildNotif(config.displayName, pct, false, speedBps, etaSec))
                    }
                }
                out.flush()
            }

            if (isStopped) "cancelled" else null
        }
    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    data class ProbeResult(val totalSize: Long, val resolvedUrl: String)

    private suspend fun probeSize(url: String, authHeader: String?): ProbeResult? = withContext(Dispatchers.IO) {
        try {
            val request = Request.Builder()
                .url(url)
                .head()
                .apply { if (!authHeader.isNullOrBlank()) addHeader("Authorization", authHeader) }
                .build()

            httpClient.newCall(request).execute().use { response ->
                if (!response.isSuccessful) return@withContext null
                val acceptsRange = response.header("Accept-Ranges")?.contains("bytes") == true
                val size         = response.header("Content-Length")?.toLongOrNull() ?: -1L
                val resolvedUrl  = response.request.url.toString()
                if (acceptsRange && size > 0) ProbeResult(size, resolvedUrl) else null
            }
        } catch (_: Exception) { null }
    }

    private fun cleanupParts(config: ModelConfig, tmpFile: File) {
        tmpFile.delete()
        (0 until PARALLEL_CHUNKS).forEach { i ->
            File(tmpFile.parent, "${config.filename}.part$i").delete()
        }
        nm.cancel(notifId)
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

    private fun buildNotif(label: String, progress: Int, indeterminate: Boolean,
                           speedBps: Long = 0, etaSec: Long = -1) =
        NotificationCompat.Builder(applicationContext, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.stat_sys_download)
            .setContentTitle("Downloading $label")
            .setContentText(when {
                indeterminate -> "Starting…"
                else          -> buildProgressText(progress, speedBps, etaSec)
            })
            .setProgress(100, progress, indeterminate)
            .setOngoing(true)
            .setSilent(true)
            .build()

    private fun buildProgressText(pct: Int, speedBps: Long, etaSec: Long): String {
        val sb = StringBuilder("$pct%")
        if (speedBps > 0) sb.append("  ·  ").append(formatSpeed(speedBps))
        if (etaSec >= 0) sb.append("  ·  ETA ").append(formatEta(etaSec))
        return sb.toString()
    }

    private fun formatSpeed(bps: Long): String = when {
        bps < 1_024     -> "$bps B/s"
        bps < 1_048_576 -> "${bps / 1_024} KB/s"
        else            -> "%.1f MB/s".format(bps.toDouble() / 1_048_576)
    }

    private fun formatEta(sec: Long): String = when {
        sec < 60    -> "${sec}s"
        sec < 3_600 -> "${sec / 60}m ${sec % 60}s"
        else        -> "${sec / 3_600}h ${(sec % 3_600) / 60}m"
    }
}
