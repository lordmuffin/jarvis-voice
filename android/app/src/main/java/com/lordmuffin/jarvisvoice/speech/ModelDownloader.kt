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
        fun onExtracting()   // kept for API compat; no longer called mid-process
        fun onComplete()
        fun onError(message: String)
    }

    private val mainHandler = Handler(Looper.getMainLooper())
    private var thread: Thread? = null
    @Volatile private var cancelled = false

    companion object {
        private const val MODEL_URL =
            "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-whisper-base.en.tar.bz2"

        private val TARGET_FILES = setOf(
            "sherpa-onnx-whisper-base.en/base.en-encoder.int8.onnx",
            "sherpa-onnx-whisper-base.en/base.en-decoder.int8.onnx",
            "sherpa-onnx-whisper-base.en/base.en-tokens.txt"
        )

        // Update UI at most every 512 KB received (avoids flooding the main thread)
        private const val NOTIFY_BYTES = 512 * 1024L
    }

    fun download(listener: Listener) {
        cancelled = false
        thread = Thread {
            try {
                val modelDir = File(context.filesDir, SherpaOnnxSpeechEngine.MODEL_SUBDIR_PUBLIC)
                    .also { it.mkdirs() }

                val conn = (URL(MODEL_URL).openConnection() as HttpURLConnection).apply {
                    connectTimeout = 15_000
                    readTimeout    = 60_000
                    connect()
                }
                if (conn.responseCode !in 200..299) {
                    mainHandler.post { listener.onError("HTTP ${conn.responseCode}") }
                    return@Thread
                }

                val total = conn.contentLengthLong
                DebugLog.i("ModelDownloader", "starting download total=$total")

                // Stream HTTP → bzip2 → tar → files in one pass.
                // No intermediate .tar.bz2 on disk: avoids the multi-minute separate extraction step.
                val counting = CountingInputStream(conn.inputStream.buffered(), total, listener)
                BZip2CompressorInputStream(counting).use { bzip ->
                    TarArchiveInputStream(bzip).use { tar ->
                        var entry = tar.nextEntry as? TarArchiveEntry
                        while (entry != null && !cancelled) {
                            if (!entry.isDirectory && entry.name in TARGET_FILES) {
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

                val allPresent = TARGET_FILES.all {
                    File(modelDir, File(it).name).let { f -> f.exists() && f.length() > 0 }
                }

                if (allPresent) {
                    DebugLog.i("ModelDownloader", "all model files present — complete")
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

    // Wraps an InputStream, counting bytes and throttling UI progress callbacks
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
