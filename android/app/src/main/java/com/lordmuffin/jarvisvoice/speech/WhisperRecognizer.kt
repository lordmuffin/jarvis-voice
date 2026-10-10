package com.lordmuffin.jarvisvoice.speech

import android.content.Context
import com.k2fsa.sherpa.onnx.FeatureConfig
import com.k2fsa.sherpa.onnx.OfflineModelConfig
import com.k2fsa.sherpa.onnx.OfflineRecognizer
import com.k2fsa.sherpa.onnx.OfflineRecognizerConfig
import com.k2fsa.sherpa.onnx.OfflineWhisperModelConfig
import com.lordmuffin.jarvisvoice.DebugLog
import com.lordmuffin.jarvisvoice.PersistentStorage

/**
 * The active offline Whisper model (sherpa-onnx), shared by dictation
 * ([SherpaOnnxSpeechEngine]) and Live sessions. Not thread-safe: call [transcribe] from one
 * thread at a time.
 */
class WhisperRecognizer private constructor(
    private var recognizer: OfflineRecognizer?,
    val activeProvider: String,
) {
    val isReady: Boolean get() = recognizer != null

    /** Transcribes 16 kHz mono PCM16; returns "" on failure or after [release]. */
    fun transcribe(samples: ShortArray): String {
        val rec = recognizer ?: return ""
        return try {
            val floats = FloatArray(samples.size) { samples[it] / 32768.0f }
            val stream = rec.createStream()
            stream.acceptWaveform(floats, SAMPLE_RATE)
            rec.decode(stream)
            val text = rec.getResult(stream).text.trim()
            stream.release()
            text
        } catch (e: Exception) {
            DebugLog.e("STT", "transcribe error", e)
            ""
        }
    }

    fun release() {
        val rec = recognizer
        recognizer = null
        rec?.release()
    }

    companion object {
        const val SAMPLE_RATE = 16_000

        /** Loads the active model, trying NNAPI first and falling back to CPU. */
        fun create(context: Context): WhisperRecognizer {
            val config = SttModelManager(context).getActiveConfig()
            if (config == null) {
                DebugLog.e("STT", "No STT model available — recognizer not initialized")
                return WhisperRecognizer(null, "cpu")
            }
            val dir = PersistentStorage.sttModelDir(context, config.subdir).absolutePath
            val whisper = OfflineWhisperModelConfig(
                encoder = "$dir/${config.encoderFile}",
                decoder = "$dir/${config.decoderFile}",
                language = "en",
                task = "transcribe"
            )
            fun cfg(provider: String) = OfflineRecognizerConfig(
                featConfig  = FeatureConfig(sampleRate = SAMPLE_RATE, featureDim = 80),
                modelConfig = OfflineModelConfig(
                    whisper    = whisper,
                    tokens     = "$dir/${config.tokensFile}",
                    numThreads = 2,
                    provider   = provider,
                    modelType  = "whisper"
                )
            )
            var provider = "nnapi"
            var recognizer = runCatching { OfflineRecognizer(config = cfg("nnapi")) }
                .onFailure { DebugLog.e("STT", "nnapi backend failed", it) }
                .getOrNull()
            if (recognizer == null) {
                provider = "cpu"
                recognizer = runCatching { OfflineRecognizer(config = cfg("cpu")) }
                    .onFailure { DebugLog.e("STT", "cpu backend also failed", it) }
                    .getOrNull()
            }
            DebugLog.i("STT", "Recognizer init: ${if (recognizer != null) "OK" else "FAILED"} provider=$provider model=${config.id}")
            return WhisperRecognizer(recognizer, provider)
        }
    }
}
