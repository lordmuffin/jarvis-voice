package com.lordmuffin.jarvisvoice.speech

import android.content.Context
import android.os.Handler
import android.os.Looper
import com.lordmuffin.jarvisvoice.DebugLog
import org.apache.commons.compress.archivers.tar.TarArchiveEntry
import org.apache.commons.compress.archivers.tar.TarArchiveInputStream
import org.apache.commons.compress.compressors.bzip2.BZip2CompressorInputStream
import java.io.File
import java.io.FileOutputStream
import java.io.InputStream
import java.net.HttpURLConnection
import java.net.URL

class ModelDownloader(private val context: Context) {

    interface Listener {
        fun onProgress(downloaded: Long, total: Long)
        fun onExtracting()
        fun onComplete()
        fun onError(message: String)
    }

    private val mainHandler = Handler(Looper.getMainLooper())
    private var thread: Thread? = null
    @Volatile private var cancelled = false

    companion object {
        private const val NOTIFY_BYTES = 512 * 1024L
    }

    fun download(config: SttModelConfig, listener: Listener) {
        cancelled = false
        thread = Thread {
            try {
                val modelDir = File(context.filesDir, config.subdir).also { it.mkdirs() }
                val targetFiles = setOf(
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
                if (conn.responseCode !in 200..299) {
                    mainHandler.post { listener.onError("HTTP ${conn.responseCode}") }
                    return@Thread
                }

                val total = conn.contentLengthLong
                DebugLog.i("ModelDownloader", "starting download model=${config.id} total=$total")

                val counting = CountingInputStream(conn.inputStream.buffered(), total, listener)
                BZip2CompressorInputStream(counting).use { bzip ->
                    TarArchiveInputStream(bzip).use { tar ->
                        var entry = tar.nextEntry as? TarArchiveEntry
                        while (entry != null && !cancelled) {
                            if (!entry.isDirectory && entry.name in targetFiles) {
                                val dest = File(modelDir, File(entry.name).name)
                                DebugLog.i("ModelDownloader", "extracting → ${dest.name}")
                                FileOutputStream(dest).use { out -> tar.copyTo(out) }
                                DebugLog.i("ModelDownloader", "wrote ${dest.length()} bytes")
                            }
                            entry = tar.nextEntry as? TarArchiveEntry
                        }
                    }
                }

                if (cancelled) return@Thread

                val allPresent = targetFiles.all {
                    File(modelDir, File(it).name).let { f -> f.exists() && f.length() > 0 }
                }

                if (allPresent) {
                    DebugLog.i("ModelDownloader", "model=${config.id} complete")
                    mainHandler.post { listener.onComplete() }
                } else {
                    DebugLog.e("ModelDownloader", "extraction finished but some files missing")
                    mainHandler.post { listener.onError("Extraction incomplete — retry") }
                }

            } catch (e: Exception) {
                DebugLog.e("ModelDownloader", "download/extract failed", e)
                if (!cancelled) mainHandler.post { listener.onError(e.message ?: "Download failed") }
            }
        }.also { it.name = "jarvis-model-dl"; it.start() }
    }

    fun cancel() {
        cancelled = true
        thread?.interrupt()
    }

    private inner class CountingInputStream(
        private val wrapped: InputStream,
        private val total: Long,
        private val listener: Listener
    ) : InputStream() {

        private var count = 0L
        private var lastNotified = 0L

        override fun read(): Int = wrapped.read().also { b -> if (b != -1) tick(1) }

        override fun read(b: ByteArray, off: Int, len: Int): Int =
            wrapped.read(b, off, len).also { n -> if (n > 0) tick(n.toLong()) }

        override fun close() = wrapped.close()

        private fun tick(n: Long) {
            count += n
            if (count - lastNotified >= NOTIFY_BYTES) {
                lastNotified = count
                val snap = count
                mainHandler.post { listener.onProgress(snap, total) }
            }
        }
    }
}
