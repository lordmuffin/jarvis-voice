package com.lordmuffin.jarvisvoice.speech

import android.content.Context
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.media.audiofx.AcousticEchoCanceler
import android.media.audiofx.NoiseSuppressor
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import com.lordmuffin.jarvisvoice.AudioDeviceRouter
import com.k2fsa.sherpa.onnx.FeatureConfig
import com.k2fsa.sherpa.onnx.OfflineModelConfig
import com.k2fsa.sherpa.onnx.OfflineRecognizer
import com.k2fsa.sherpa.onnx.OfflineRecognizerConfig
import com.k2fsa.sherpa.onnx.OfflineWhisperModelConfig
import com.lordmuffin.jarvisvoice.DebugLog
import com.lordmuffin.jarvisvoice.PersistentStorage
import java.io.File
import java.util.concurrent.Executors

class SherpaOnnxSpeechEngine(private val context: Context) : SpeechEngine {

    var activeProvider: String = "cpu"
        private set

    private var recognizer: OfflineRecognizer? = null
    private var audioRecord: AudioRecord? = null
    private var aec: AcousticEchoCanceler? = null
    private var noiseSuppressor: NoiseSuppressor? = null
    private var recordingThread: Thread? = null
    private val deviceRouter = AudioDeviceRouter(context)
    @Volatile private var scoStarted = false

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
    private val FLUSH_SILENCE_CHUNKS    = 10             // 10 × 300ms = 3s pause → commit
    private val MAX_BUFFER_SAMPLES      = sampleRate * 20 // 20s without pause → force commit
    private val MIN_CHUNK_SAMPLES       = sampleRate / 2  // 0.5s minimum to transcribe
    private val AUTO_STOP_EMPTY_FLUSHES = 20             // 20 × ~3s = ~60s pure silence

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
        fun isModelAvailable(context: Context): Boolean =
            SttModelManager(context).getActiveConfig() != null
    }

    init {
        initRecognizer()
    }

    private fun initRecognizer() {
        val config = SttModelManager(context).getActiveConfig()
        if (config == null) {
            DebugLog.e("STT", "No STT model available — recognizer not initialized")
            return
        }
        val dir = PersistentStorage.sttModelDir(context, config.subdir).absolutePath
        val whisper = OfflineWhisperModelConfig(
            encoder = "$dir/${config.encoderFile}",
            decoder = "$dir/${config.decoderFile}",
            language = "en",
            task = "transcribe"
        )
        fun cfg(provider: String) = OfflineRecognizerConfig(
            featConfig  = FeatureConfig(sampleRate = sampleRate, featureDim = 80),
            modelConfig = OfflineModelConfig(
                whisper    = whisper,
                tokens     = "$dir/${config.tokensFile}",
                numThreads = 2,
                provider   = provider,
                modelType  = "whisper"
            )
        )
        val nnapiResult = runCatching { OfflineRecognizer(config = cfg("nnapi")) }
        if (nnapiResult.isSuccess) {
            recognizer     = nnapiResult.getOrThrow()
            activeProvider = "nnapi"
        } else {
            DebugLog.e("STT", "nnapi backend failed", nnapiResult.exceptionOrNull())
            val cpuResult = runCatching { OfflineRecognizer(config = cfg("cpu")) }
            recognizer     = cpuResult.getOrNull()
            activeProvider = "cpu"
            if (cpuResult.isFailure) DebugLog.e("STT", "cpu backend also failed", cpuResult.exceptionOrNull())
        }

        DebugLog.i("STT", "Recognizer init: ${if (recognizer != null) "OK" else "FAILED"} provider=$activeProvider model=${config.id}")
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

        // Route to preferred microphone (earbuds, USB, etc.)
        val preferredDevice = deviceRouter.getPreferredDevice()
        if (preferredDevice != null && deviceRouter.isBluetoothSco(preferredDevice)) {
            deviceRouter.startBluetoothSco()
            scoStarted = deviceRouter.waitForSco(2_000L)
            DebugLog.i("STT", "Bluetooth SCO ready=$scoStarted")
        }

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
        if (preferredDevice != null && Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            audioRecord?.preferredDevice = preferredDevice
            DebugLog.i("STT", "preferred input device: ${deviceRouter.deviceLabel(preferredDevice)}")
        }
        // Attach AEC and NS to suppress TTS speaker bleed (enables auto barge-in).
        val sessionId = audioRecord!!.audioSessionId
        if (AcousticEchoCanceler.isAvailable()) {
            aec = AcousticEchoCanceler.create(sessionId)
            DebugLog.i("STT", "AEC attached session=$sessionId")
        }
        if (NoiseSuppressor.isAvailable()) {
            noiseSuppressor = NoiseSuppressor.create(sessionId)
            DebugLog.i("STT", "NoiseSuppressor attached")
        }
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

        // In VAD conversation mode (holdMode=false), each committed speech chunk
        // is a complete utterance — auto-fire onFinal so the caller can send to
        // the LLM immediately without waiting for 30s silence or stopListening().
        // Guard with pendingFinal so a concurrent stopListening() call doesn't
        // double-fire the callback.
        if (!holdMode && text.isNotBlank() && !pendingFinal) {
            val final = committedText.trim()
            committedText = ""
            pendingFinal = true
            isListening  = false
            DebugLog.i("STT", "auto-final on VAD: \"${final.take(60)}\"")
            Thread({
                recordingThread?.join(600)
                aec?.release(); aec = null
                noiseSuppressor?.release(); noiseSuppressor = null
                audioRecord?.run { stop(); release() }
                audioRecord = null
                if (scoStarted) {
                    deviceRouter.stopBluetoothSco()
                    scoStarted = false
                }
            }, "jarvis-auto-final").start()
            mainHandler.post { onFinalCallback?.invoke(final) }
        } else {
            val partial = committedText
            mainHandler.post { onPartialCallback?.invoke(partial) }
        }
    }

    override fun stopListening() {
        DebugLog.i("STT", "stopListening pendingFinal=$pendingFinal committedLen=${committedText.length}")
        if (pendingFinal) return
        pendingFinal = true
        isListening  = false
        // Do NOT call audioRecord.stop() here — the recording thread may be mid-read (≤300ms).
        // Stopping the hardware now truncates the tail of the utterance. Instead, signal the
        // loop to exit via isListening=false, wait for the in-flight read to finish, then stop.
        Thread({
            recordingThread?.join(500)  // at most one 300ms chunk left to complete
            audioRecord?.stop()

            val snapshot: ShortArray
            synchronized(bufferLock) {
                snapshot = currentBuffer.toShortArray()
                currentBuffer.clear()
            }

            // Read committedText INSIDE the executor so this job runs after any in-flight
            // flushChunk jobs finish. Reading it outside would race — the captured value
            // would be stale if a flush was still processing when stopListening() was called.
            transcribeExecutor.submit {
                val lastChunk = if (snapshot.size >= MIN_CHUNK_SAMPLES) transcribe(snapshot) else ""
                DebugLog.i("STT", "stopListening lastChunk: \"${lastChunk.take(60)}\"")
                val committed = committedText
                val sep   = if (committed.isNotEmpty() && lastChunk.isNotBlank()) " " else ""
                val final = (committed + sep + lastChunk).trim()
                DebugLog.i("STT", "onFinal total words=${final.split(" ").size}")
                mainHandler.post { onFinalCallback?.invoke(final) }
                if (scoStarted) {
                    deviceRouter.stopBluetoothSco()
                    scoStarted = false
                }
            }
        }, "jarvis-stop").start()
    }

    override fun cancelListening() {
        DebugLog.i("STT", "cancelListening")
        isListening  = false
        pendingFinal = true  // suppress any in-flight auto-final callback
        Thread({
            recordingThread?.join(500)
            aec?.release(); aec = null
            noiseSuppressor?.release(); noiseSuppressor = null
            audioRecord?.run { stop(); release() }
            audioRecord = null
            if (scoStarted) { deviceRouter.stopBluetoothSco(); scoStarted = false }
        }, "jarvis-cancel").start()
    }

    override fun destroy() {
        DebugLog.i("STT", "destroy")
        isListening = false
        aec?.release(); aec = null
        noiseSuppressor?.release(); noiseSuppressor = null
        audioRecord?.run { stop(); release() }
        audioRecord = null
        recordingThread = null
        transcribeExecutor.shutdown()
        recognizer?.release()
        recognizer = null
        if (scoStarted) {
            deviceRouter.stopBluetoothSco()
            scoStarted = false
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
