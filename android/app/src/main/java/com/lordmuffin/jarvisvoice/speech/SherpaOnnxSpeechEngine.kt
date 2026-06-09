package com.lordmuffin.jarvisvoice.speech

import android.content.Context
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import com.k2fsa.sherpa.onnx.FeatureConfig
import com.k2fsa.sherpa.onnx.OfflineModelConfig
import com.k2fsa.sherpa.onnx.OfflineRecognizer
import com.k2fsa.sherpa.onnx.OfflineRecognizerConfig
import com.k2fsa.sherpa.onnx.OfflineWhisperModelConfig
import java.io.File
import java.io.FileOutputStream
import java.util.concurrent.Executors

class SherpaOnnxSpeechEngine(private val context: Context) : SpeechEngine {

    private var recognizer: OfflineRecognizer? = null
    private var audioRecord: AudioRecord? = null
    private var recordingThread: Thread? = null

    @Volatile private var isListening = false
    @Volatile private var pendingFinal = false

    private val mainHandler = Handler(Looper.getMainLooper())
    private val transcribeExecutor = Executors.newSingleThreadExecutor()

    private val sampleRate = 16000
    // 300ms chunks at 16kHz mono
    private val chunkSamples = sampleRate * 300 / 1000

    private var onPartialCallback: ((String) -> Unit)? = null
    private var onFinalCallback: ((String) -> Unit)? = null
    private var onErrorCallback: ((Int) -> Unit)? = null

    private val bufferLock = Any()
    private val allSamples = ArrayList<Short>(sampleRate * 30)  // pre-alloc 30s

    private var consecutiveSilenceChunks = 0
    private val silenceRmsThreshold = 200.0     // below this RMS → silence
    private val silenceChunksForFinal = 2       // 2 × 300ms = 600ms quiet → final

    private var lastPartialTimestamp = 0L
    private val partialIntervalMs = 1500L       // interim result every 1.5s

    companion object {
        const val MODEL_SUBDIR_PUBLIC = "models/whisper-base-en"
        private const val MODEL_SUBDIR = MODEL_SUBDIR_PUBLIC

        // Actual filenames used by sherpa-onnx whisper-base.en
        private val MODEL_FILES = listOf(
            "base.en-encoder.int8.onnx",
            "base.en-decoder.int8.onnx",
            "base.en-tokens.txt"
        )

        fun isModelAvailable(context: Context): Boolean {
            val dir = File(context.filesDir, MODEL_SUBDIR)
            return MODEL_FILES.all { File(dir, it).exists() }
        }

        // Called in init{} — copies bundled assets to filesDir so ONNX Runtime can
        // open them by file path (it cannot read from APK assets directly).
        private fun copyAssetsToFilesDir(context: Context) {
            val dest = File(context.filesDir, MODEL_SUBDIR).also { it.mkdirs() }
            MODEL_FILES.forEach { name ->
                val target = File(dest, name)
                if (target.exists()) return@forEach
                try {
                    context.assets.open("$MODEL_SUBDIR/$name").use { src ->
                        FileOutputStream(target).use { src.copyTo(it) }
                    }
                } catch (_: Exception) { /* model not bundled; isModelAvailable() returns false */ }
            }
        }
    }

    init {
        copyAssetsToFilesDir(context)
        initRecognizer()
    }

    private fun initRecognizer() {
        val dir = File(context.filesDir, MODEL_SUBDIR).absolutePath

        val whisper = OfflineWhisperModelConfig(
            encoder = "$dir/base.en-encoder.int8.onnx",
            decoder = "$dir/base.en-decoder.int8.onnx",
            language = "en",
            task = "transcribe"
        )

        fun cfg(provider: String) = OfflineRecognizerConfig(
            featConfig = FeatureConfig(sampleRate = sampleRate, featureDim = 80),
            modelConfig = OfflineModelConfig(
                whisper = whisper,
                tokens = "$dir/base.en-tokens.txt",
                numThreads = 2,
                provider = provider,
                modelType = "whisper"
            )
        )

        // Prefer NNAPI (Tensor G5 NPU), fall back to CPU.
        recognizer = runCatching { OfflineRecognizer(config = cfg("nnapi")) }.getOrNull()
            ?: runCatching { OfflineRecognizer(config = cfg("cpu")) }.getOrNull()
    }

    override fun startListening(
        onPartial: (String) -> Unit,
        onFinal: (String) -> Unit,
        onError: (Int) -> Unit
    ) {
        if (recognizer == null) { onError(-1); return }

        onPartialCallback = onPartial
        onFinalCallback = onFinal
        onErrorCallback = onError

        isListening = true
        pendingFinal = false
        consecutiveSilenceChunks = 0
        lastPartialTimestamp = 0L
        synchronized(bufferLock) { allSamples.clear() }

        val minBuf = AudioRecord.getMinBufferSize(
            sampleRate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT
        )
        audioRecord = AudioRecord(
            MediaRecorder.AudioSource.VOICE_RECOGNITION,
            sampleRate,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
            maxOf(minBuf, chunkSamples * 4)
        )
        audioRecord?.startRecording()

        recordingThread = Thread {
            val chunk = ShortArray(chunkSamples)
            while (isListening) {
                val read = audioRecord?.read(chunk, 0, chunkSamples) ?: break
                if (read <= 0) continue

                val samples = chunk.copyOf(read)
                synchronized(bufferLock) {
                    // Cap at 30s to bound memory; trim oldest 5s when full
                    if (allSamples.size > sampleRate * 30) {
                        val drop = sampleRate * 5
                        repeat(drop) { allSamples.removeAt(0) }
                    }
                    samples.forEach { allSamples.add(it) }
                }

                val silent = rms(samples) < silenceRmsThreshold
                if (silent) consecutiveSilenceChunks++ else consecutiveSilenceChunks = 0

                val bufSize = synchronized(bufferLock) { allSamples.size }
                val now = SystemClock.elapsedRealtime()
                val dueForPartial = !pendingFinal &&
                    bufSize > sampleRate &&   // at least 1s recorded
                    (now - lastPartialTimestamp) >= partialIntervalMs

                if (!pendingFinal && consecutiveSilenceChunks >= silenceChunksForFinal) {
                    pendingFinal = true
                    isListening = false
                    dispatchTranscription(isFinal = true)
                } else if (dueForPartial) {
                    lastPartialTimestamp = now
                    dispatchTranscription(isFinal = false)
                }
            }
        }.also { it.name = "jarvis-record"; it.start() }
    }

    private fun dispatchTranscription(isFinal: Boolean) {
        val snapshot: ShortArray
        synchronized(bufferLock) { snapshot = allSamples.toShortArray() }
        if (snapshot.isEmpty()) {
            if (isFinal) mainHandler.post { onFinalCallback?.invoke("") }
            return
        }
        transcribeExecutor.submit {
            val text = transcribe(snapshot)
            mainHandler.post {
                if (isFinal) onFinalCallback?.invoke(text)
                else if (text.isNotBlank()) onPartialCallback?.invoke(text)
            }
        }
    }

    private fun transcribe(samples: ShortArray): String {
        val rec = recognizer ?: return ""
        return try {
            val floats = FloatArray(samples.size) { samples[it] / 32768.0f }
            val stream = rec.createStream()
            stream.acceptWaveform(floats, sampleRate)
            rec.decode(stream)
            val text = rec.getResult(stream).text.trim()
            stream.release()
            text
        } catch (_: Exception) { "" }
    }

    override fun stopListening() {
        isListening = false
        audioRecord?.stop()
        if (!pendingFinal) {
            pendingFinal = true
            dispatchTranscription(isFinal = true)
        }
    }

    override fun destroy() {
        isListening = false
        audioRecord?.run { stop(); release() }
        audioRecord = null
        recordingThread = null
        transcribeExecutor.shutdown()
        recognizer?.release()
        recognizer = null
    }

    private fun rms(samples: ShortArray): Double {
        if (samples.isEmpty()) return 0.0
        var sum = 0.0
        for (s in samples) sum += s.toDouble() * s.toDouble()
        return Math.sqrt(sum / samples.size)
    }
}
