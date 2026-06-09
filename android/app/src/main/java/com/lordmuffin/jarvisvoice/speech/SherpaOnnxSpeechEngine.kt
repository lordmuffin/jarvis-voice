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
import com.lordmuffin.jarvisvoice.DebugLog
import java.io.File
import java.io.FileOutputStream
import java.util.concurrent.Executors

class SherpaOnnxSpeechEngine(private val context: Context) : SpeechEngine {

    private var recognizer: OfflineRecognizer? = null
    private var audioRecord: AudioRecord? = null
    private var recordingThread: Thread? = null

    @Volatile private var isListening  = false
    @Volatile private var pendingFinal = false
    @Volatile private var holdMode     = false

    private val mainHandler       = Handler(Looper.getMainLooper())
    private val transcribeExecutor = Executors.newSingleThreadExecutor { r ->
        Thread(r, "jarvis-transcribe")
    }

    private val sampleRate   = 16000
    private val chunkSamples = sampleRate * 300 / 1000   // 300 ms per read

    // VAD-flush thresholds
    private val FLUSH_SILENCE_CHUNKS    = 5              // 5 × 300ms = 1.5s pause → commit
    private val MAX_BUFFER_SAMPLES      = sampleRate * 15 // 15s without pause → force commit
    private val MIN_CHUNK_SAMPLES       = sampleRate / 2  // 0.5s minimum to transcribe
    private val AUTO_STOP_EMPTY_FLUSHES = 20             // 20 × ~1.5s = 30s pure silence

    private val silenceRmsThreshold  = 200.0
    private val partialIntervalMs    = 2_000L

    private var silenceRunChunks     = 0
    private var emptyFlushCount      = 0
    private var lastPartialTimestamp = 0L

    // Text accumulated from flushed chunks; only written from transcribeExecutor
    @Volatile private var committedText = ""

    private val bufferLock    = Any()
    private val currentBuffer = ArrayList<Short>(sampleRate * 5)

    private var onPartialCallback: ((String) -> Unit)? = null
    private var onFinalCallback:   ((String) -> Unit)? = null
    private var onErrorCallback:   ((Int)    -> Unit)? = null

    companion object {
        const val MODEL_SUBDIR_PUBLIC = "models/whisper-base-en"
        private const val MODEL_SUBDIR = MODEL_SUBDIR_PUBLIC

        private val MODEL_FILES = listOf(
            "base.en-encoder.int8.onnx",
            "base.en-decoder.int8.onnx",
            "base.en-tokens.txt"
        )

        fun isModelAvailable(context: Context): Boolean {
            val dir = File(context.filesDir, MODEL_SUBDIR)
            return MODEL_FILES.all { File(dir, it).exists() }
        }

        private fun copyAssetsToFilesDir(context: Context) {
            val dest = File(context.filesDir, MODEL_SUBDIR).also { it.mkdirs() }
            MODEL_FILES.forEach { name ->
                val target = File(dest, name)
                if (target.exists()) return@forEach
                try {
                    context.assets.open("$MODEL_SUBDIR/$name").use { src ->
                        FileOutputStream(target).use { src.copyTo(it) }
                    }
                } catch (_: Exception) {}
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
            featConfig  = FeatureConfig(sampleRate = sampleRate, featureDim = 80),
            modelConfig = OfflineModelConfig(
                whisper    = whisper,
                tokens     = "$dir/base.en-tokens.txt",
                numThreads = 2,
                provider   = provider,
                modelType  = "whisper"
            )
        )
        recognizer = runCatching { OfflineRecognizer(config = cfg("nnapi")) }.getOrNull()
            ?: runCatching { OfflineRecognizer(config = cfg("cpu")) }.getOrNull()

        DebugLog.i("STT", "Recognizer init: ${if (recognizer != null) "OK" else "FAILED"}")
    }

    override fun startListening(
        onPartial: (String) -> Unit,
        onFinal:   (String) -> Unit,
        onError:   (Int)    -> Unit,
        holdMode:  Boolean
    ) {
        if (recognizer == null) {
            DebugLog.e("STT", "startListening called but recognizer is null")
            onError(-1)
            return
        }

        onPartialCallback = onPartial
        onFinalCallback   = onFinal
        onErrorCallback   = onError
        this.holdMode     = holdMode

        isListening      = true
        pendingFinal     = false
        silenceRunChunks = 0
        emptyFlushCount  = 0
        lastPartialTimestamp = 0L
        committedText    = ""
        synchronized(bufferLock) { currentBuffer.clear() }

        DebugLog.i("STT", "startListening holdMode=$holdMode")

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
                val silent  = rms(samples) < silenceRmsThreshold

                if (!silent) {
                    silenceRunChunks = 0
                    emptyFlushCount  = 0
                } else {
                    silenceRunChunks++
                }

                synchronized(bufferLock) { samples.forEach { currentBuffer.add(it) } }

                val bufSize = synchronized(bufferLock) { currentBuffer.size }

                val vadFlush   = silenceRunChunks >= FLUSH_SILENCE_CHUNKS
                val sizeFlush  = bufSize >= MAX_BUFFER_SAMPLES
                val shouldFlush = !pendingFinal && (vadFlush || sizeFlush)

                if (shouldFlush) {
                    silenceRunChunks = 0
                    val snapshot: ShortArray
                    synchronized(bufferLock) {
                        snapshot = currentBuffer.toShortArray()
                        currentBuffer.clear()
                    }
                    if (snapshot.size >= MIN_CHUNK_SAMPLES) {
                        emptyFlushCount = 0
                        DebugLog.i("STT", "flush chunk ${snapshot.size} samples (${snapshot.size / sampleRate}s)")
                        transcribeExecutor.submit { flushChunk(snapshot) }
                    } else {
                        emptyFlushCount++
                        DebugLog.i("STT", "empty flush #$emptyFlushCount holdMode=$holdMode")
                        if (!holdMode && emptyFlushCount >= AUTO_STOP_EMPTY_FLUSHES) {
                            DebugLog.i("STT", "auto-stop: 30s silence reached")
                            pendingFinal = true
                            isListening  = false
                            val committed = committedText
                            mainHandler.post { onFinalCallback?.invoke(committed.trim()) }
                        }
                    }
                } else if (!pendingFinal) {
                    val now = SystemClock.elapsedRealtime()
                    if (bufSize > MIN_CHUNK_SAMPLES &&
                        (now - lastPartialTimestamp) >= partialIntervalMs
                    ) {
                        lastPartialTimestamp = now
                        val snap: ShortArray
                        synchronized(bufferLock) { snap = currentBuffer.toShortArray() }
                        val committed = committedText
                        transcribeExecutor.submit {
                            val current = transcribe(snap)
                            val full = buildString {
                                if (committed.isNotEmpty()) append(committed).append(' ')
                                if (current.isNotBlank()) append(current.trim())
                            }.trim()
                            if (full.isNotBlank()) mainHandler.post { onPartialCallback?.invoke(full) }
                        }
                    }
                }
            }
            DebugLog.i("STT", "recording loop exited isListening=$isListening")
        }.also { it.name = "jarvis-record"; it.start() }
    }

    // Runs on transcribeExecutor — safe to mutate committedText
    private fun flushChunk(snapshot: ShortArray) {
        val text = transcribe(snapshot)
        DebugLog.i("STT", "flushChunk result: \"${text.take(60)}\"")
        if (text.isNotBlank()) {
            val sep = if (committedText.isNotEmpty()) " " else ""
            committedText += sep + text.trim()
        }
        val partial = committedText
        mainHandler.post { onPartialCallback?.invoke(partial) }
    }

    override fun stopListening() {
        DebugLog.i("STT", "stopListening pendingFinal=$pendingFinal committedLen=${committedText.length}")
        isListening = false
        audioRecord?.stop()
        if (pendingFinal) return
        pendingFinal = true

        val snapshot: ShortArray
        synchronized(bufferLock) {
            snapshot = currentBuffer.toShortArray()
            currentBuffer.clear()
        }
        val committed = committedText

        transcribeExecutor.submit {
            val lastChunk = if (snapshot.size >= MIN_CHUNK_SAMPLES) transcribe(snapshot) else ""
            DebugLog.i("STT", "stopListening lastChunk: \"${lastChunk.take(60)}\"")
            val sep   = if (committed.isNotEmpty() && lastChunk.isNotBlank()) " " else ""
            val final = (committed + sep + lastChunk).trim()
            DebugLog.i("STT", "onFinal total words=${final.split(" ").size}")
            mainHandler.post { onFinalCallback?.invoke(final) }
        }
    }

    override fun destroy() {
        DebugLog.i("STT", "destroy")
        isListening = false
        audioRecord?.run { stop(); release() }
        audioRecord = null
        recordingThread = null
        transcribeExecutor.shutdown()
        recognizer?.release()
        recognizer = null
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
        } catch (e: Exception) {
            DebugLog.e("STT", "transcribe error", e)
            ""
        }
    }

    private fun rms(samples: ShortArray): Double {
        if (samples.isEmpty()) return 0.0
        var sum = 0.0
        for (s in samples) sum += s.toDouble() * s.toDouble()
        return Math.sqrt(sum / samples.size)
    }
}
