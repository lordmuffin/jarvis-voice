package com.lordmuffin.jarvisvoice.speech

import android.content.Context
import android.os.Handler
import android.os.Looper
import org.apache.commons.compress.archivers.tar.TarArchiveEntry
import org.apache.commons.compress.archivers.tar.TarArchiveInputStream
import org.apache.commons.compress.compressors.bzip2.BZip2CompressorInputStream
import java.io.File
import java.io.FileOutputStream
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
        private const val MODEL_URL =
            "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-whisper-base.en.tar.bz2"

        private val TARGET_FILES = setOf(
            "sherpa-onnx-whisper-base.en/base.en-encoder.int8.onnx",
            "sherpa-onnx-whisper-base.en/base.en-decoder.int8.onnx",
            "sherpa-onnx-whisper-base.en/base.en-tokens.txt"
        )
    }

    fun download(listener: Listener) {
        cancelled = false
        thread = Thread {
            val tmpTarball = File(context.cacheDir, "sherpa-whisper.tar.bz2")
            try {
                val modelDir = File(context.filesDir, SherpaOnnxSpeechEngine.MODEL_SUBDIR_PUBLIC)
                    .also { it.mkdirs() }

                // ── Download ─────────────────────────────────────────────
                val conn = (URL(MODEL_URL).openConnection() as HttpURLConnection).apply {
                    connectTimeout = 15_000
                    readTimeout    = 60_000
                    connect()
                }
                val total = conn.contentLengthLong
                var received = 0L
                val buf = ByteArray(64 * 1024)

                FileOutputStream(tmpTarball).use { out ->
                    conn.inputStream.use { inp ->
                        var n: Int
                        while (inp.read(buf).also { n = it } != -1) {
                            if (cancelled) return@Thread
                            out.write(buf, 0, n)
                            received += n
                            val snap = received
                            mainHandler.post { listener.onProgress(snap, total) }
                        }
                    }
                }
                if (cancelled) return@Thread

                // ── Extract ──────────────────────────────────────────────
                mainHandler.post { listener.onExtracting() }

                BZip2CompressorInputStream(tmpTarball.inputStream().buffered()).use { bzip ->
                    TarArchiveInputStream(bzip).use { tar ->
                        var entry = tar.nextEntry as? TarArchiveEntry
                        while (entry != null) {
                            if (!cancelled && !entry.isDirectory && entry.name in TARGET_FILES) {
                                val dest = File(modelDir, File(entry.name).name)
                                FileOutputStream(dest).use { out -> tar.copyTo(out) }
                            }
                            entry = tar.nextEntry as? TarArchiveEntry
                        }
                    }
                }

                tmpTarball.delete()

                if (!cancelled) mainHandler.post { listener.onComplete() }

            } catch (e: Exception) {
                tmpTarball.delete()
                if (!cancelled) mainHandler.post { listener.onError(e.message ?: "Download failed") }
            }
        }.also { it.name = "jarvis-model-dl"; it.start() }
    }

    fun cancel() {
        cancelled = true
        thread?.interrupt()
    }
}
