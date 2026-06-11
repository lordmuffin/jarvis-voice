package com.lordmuffin.jarvisvoice.speech

import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.content.pm.ServiceInfo
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.work.*
import com.lordmuffin.jarvisvoice.DebugLog
import com.lordmuffin.jarvisvoice.JarvisApp
import com.lordmuffin.jarvisvoice.ModelDownloadWorker
import com.lordmuffin.jarvisvoice.PersistentStorage
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.apache.commons.compress.archivers.tar.TarArchiveEntry
import org.apache.commons.compress.archivers.tar.TarArchiveInputStream
import org.apache.commons.compress.compressors.bzip2.BZip2CompressorInputStream
import java.io.File
import java.io.FileOutputStream
import java.io.InputStream
import java.net.HttpURLConnection
import java.net.URL

class SttDownloadWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {

    companion object {
        const val KEY_MODEL_ID   = "stt_model_id"
        const val KEY_PROGRESS   = "progress"    // Int 0–100 or -1 (indeterminate)
        const val KEY_EXTRACTING = "extracting"  // Boolean
        const val KEY_SPEED_BPS  = "speed_bps"   // Long bytes/sec
        const val KEY_ETA_SEC    = "eta_sec"      // Long seconds, -1 if unknown
        const val KEY_ERROR      = "error"

        fun workName(modelId: String) = "stt_download_$modelId"

        fun enqueue(context: Context, modelId: String) {
            val req = OneTimeWorkRequestBuilder<SttDownloadWorker>()
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

    private val nm      get() = applicationContext.getSystemService(android.app.NotificationManager::class.java)
    private val notifId = 300 + (id.hashCode() and 0xFF)

    override suspend fun getForegroundInfo(): ForegroundInfo {
        // Must create the channel here — SttDownloadWorker shares ModelDownloadWorker's
        // channel ID but doesn't go through ModelDownloadWorker.createChannel(). If the
        // user has never triggered an LLM download, the channel won't exist, and
        // setForeground() will throw "Bad notification for startForeground" killing the process.
        nm.createNotificationChannel(
            NotificationChannel(
                ModelDownloadWorker.CHANNEL_ID,
                "Model Downloads",
                NotificationManager.IMPORTANCE_LOW
            ).also { it.description = "Model download progress" }
        )
        val modelId = inputData.getString(KEY_MODEL_ID) ?: "STT Model"
        val label   = SttModelRegistry.MODELS.find { it.id == modelId }?.displayName ?: modelId
        val notif   = buildNotif(label, -1, false)
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q)
            ForegroundInfo(notifId, notif, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        else
            ForegroundInfo(notifId, notif)
    }

    override suspend fun doWork(): Result {
        val modelId = inputData.getString(KEY_MODEL_ID)
            ?: return Result.failure(workDataOf(KEY_ERROR to "no model_id"))
        val config  = SttModelRegistry.MODELS.find { it.id == modelId }
            ?: return Result.failure(workDataOf(KEY_ERROR to "unknown model: $modelId"))

        DebugLog.i("SttDL", "start $modelId")

        return try {
            setForeground(getForegroundInfo())
            withContext(Dispatchers.IO) { download(config) }
        } catch (e: Exception) {
            DebugLog.e("SttDL", "failed", e)
            if (isStopped) Result.success()
            else Result.failure(workDataOf(KEY_ERROR to (e.message ?: "Download failed")))
        } finally {
            nm.cancel(notifId)
        }
    }

    private suspend fun download(config: SttModelConfig): Result {
        val modelDir = PersistentStorage.sttModelDir(applicationContext, config.subdir)
        val targets  = setOf(
            "${config.tarSubdir}/${config.encoderFile}",
            "${config.tarSubdir}/${config.decoderFile}",
            "${config.tarSubdir}/${config.tokensFile}",
        )

        val conn = (URL(config.tarUrl).openConnection() as HttpURLConnection).apply {
            connectTimeout = 15_000
            readTimeout    = 60_000
            instanceFollowRedirects = true
            connect()
        }
        if (conn.responseCode !in 200..299)
            return Result.failure(workDataOf(KEY_ERROR to "HTTP ${conn.responseCode}"))

        val totalBytes    = conn.contentLengthLong
        var downloaded    = 0L
        var lastNotified  = 0L
        val notifyEvery   = 524_288L   // update every 512 KB for smoother speed display

        // Rolling speed window — track (bytes, timeMs) samples over last 3 seconds
        data class Sample(val bytes: Long, val ms: Long)
        val speedWin = ArrayDeque<Sample>()

        // Counting InputStream: increments `downloaded` as HTTP bytes are read.
        // Runs on the same IO thread as the coroutine body, so no sync needed.
        val counting = object : InputStream() {
            val src = conn.inputStream.buffered()
            override fun read(): Int = src.read().also { if (it != -1) downloaded++ }
            override fun read(b: ByteArray, off: Int, len: Int): Int =
                src.read(b, off, len).also { n -> if (n > 0) downloaded += n }
            override fun close() = src.close()
        }

        counting.use { src ->
            BZip2CompressorInputStream(src).use { bzip ->
                TarArchiveInputStream(bzip).use { tar ->
                    var entry: TarArchiveEntry? = tar.nextEntry
                    while (entry != null && !isStopped) {
                        if (!entry.isDirectory && entry.name in targets) {
                            val dest = File(modelDir, File(entry.name).name)
                            DebugLog.i("SttDL", "extracting → ${dest.name}")
                            FileOutputStream(dest).use { out ->
                                val buf = ByteArray(256 * 1024)
                                var n: Int
                                // Read in chunks to report progress mid-file
                                while (tar.read(buf).also { n = it } > 0 && !isStopped) {
                                    out.write(buf, 0, n)
                                    if (downloaded - lastNotified >= notifyEvery) {
                                        lastNotified = downloaded
                                        val nowMs = System.currentTimeMillis()
                                        speedWin.addLast(Sample(downloaded, nowMs))
                                        while (speedWin.size > 1 && nowMs - speedWin.first().ms > 3_000L)
                                            speedWin.removeFirst()
                                        val winMs = maxOf(1L, speedWin.last().ms - speedWin.first().ms)
                                        val speedBps = (speedWin.last().bytes - speedWin.first().bytes) * 1000 / winMs
                                        val pct = if (totalBytes > 0) (downloaded * 100 / totalBytes).toInt() else -1
                                        val etaSec = if (speedBps > 0 && totalBytes > 0) (totalBytes - downloaded) / speedBps else -1L
                                        setProgress(workDataOf(
                                            KEY_PROGRESS   to pct,
                                            KEY_EXTRACTING to false,
                                            KEY_SPEED_BPS  to speedBps,
                                            KEY_ETA_SEC    to etaSec
                                        ))
                                        nm.notify(notifId, buildNotif(config.displayName, pct, false, speedBps, etaSec))
                                    }
                                }
                            }
                            DebugLog.i("SttDL", "wrote ${File(modelDir, File(entry.name).name).length()} bytes")
                        }
                        entry = tar.nextEntry
                    }
                }
            }
        }

        if (isStopped) return Result.success()

        val allPresent = targets.all { path ->
            File(modelDir, File(path).name).let { it.exists() && it.length() > 0 }
        }
        return if (allPresent) {
            DebugLog.i("SttDL", "${config.id} complete")
            Result.success()
        } else {
            Result.failure(workDataOf(KEY_ERROR to "Extraction incomplete — retry"))
        }
    }

    private fun buildNotif(label: String, pct: Int, extracting: Boolean,
                           speedBps: Long = 0, etaSec: Long = -1) =
        NotificationCompat.Builder(applicationContext, ModelDownloadWorker.CHANNEL_ID)
            .setSmallIcon(android.R.drawable.stat_sys_download)
            .setContentTitle(if (extracting) "Extracting $label" else "Downloading $label")
            .setContentText(when {
                extracting -> "Extracting model files…"
                pct < 0    -> "Connecting…"
                else       -> buildProgressText(pct, speedBps, etaSec)
            })
            .setProgress(100, maxOf(0, pct), pct < 0 || extracting)
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
        bps < 1_024         -> "$bps B/s"
        bps < 1_048_576     -> "${bps / 1_024} KB/s"
        else                -> "%.1f MB/s".format(bps.toDouble() / 1_048_576)
    }

    private fun formatEta(sec: Long): String = when {
        sec < 60    -> "${sec}s"
        sec < 3_600 -> "${sec / 60}m ${sec % 60}s"
        else        -> "${sec / 3_600}h ${(sec % 3_600) / 60}m"
    }
}
