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
 * A handle on the active offline Whisper model (sherpa-onnx), shared by dictation
 * ([SherpaOnnxSpeechEngine]) and Live sessions.
 *
 * The native model is loaded once per process and reference-counted: the overlay service keeps
 * dictation's handle for its whole lifetime, so a Live session that loaded its own copy would hold
 * two models in memory, enough to get the process killed with the larger models. Native calls are
 * serialized, and the model is freed only after the last handle is released and any in-flight
 * transcription has returned.
 */
class WhisperRecognizer private constructor(@Volatile private var model: Model?) {

    val isReady: Boolean get() = model != null

    val activeProvider: String get() = model?.provider ?: "cpu"

    /** Transcribes 16 kHz mono PCM16; returns "" on failure or after [release]. */
    fun transcribe(samples: ShortArray): String = model?.transcribe(samples) ?: ""

    /** Gives up this handle. Idempotent. */
    fun release() {
        val m = synchronized(this) { model.also { model = null } } ?: return
        unref(m)
    }

    /** One loaded native recognizer. */
    private class Model(val id: String, private var recognizer: OfflineRecognizer?, val provider: String) {
        var refs = 0

        @Synchronized
        fun transcribe(samples: ShortArray): String {
            val rec = recognizer ?: return ""
            return try {
                val floats = FloatArray(samples.size) { samples[it] / 32768.0f }
                val stream = rec.createStream()
                try {
                    stream.acceptWaveform(floats, SAMPLE_RATE)
                    rec.decode(stream)
                    rec.getResult(stream).text.trim()
                } finally {
                    stream.release()
                }
            } catch (e: Exception) {
                DebugLog.e("STT", "transcribe error", e)
                ""
            }
        }

        /** Waits for an in-flight [transcribe] (same monitor), then frees the native model. */
        @Synchronized
        fun free() {
            val rec = recognizer
            recognizer = null
            rec?.release()
        }
    }

    companion object {
        const val SAMPLE_RATE = 16_000

        private val lock = Any()
        /** The most recently loaded model; older ones live on until their last handle is released. */
        private var current: Model? = null

        /**
         * Returns a handle on the active model, loading it (NNAPI first, then CPU) unless it is
         * already loaded. The handle must be [release]d. Blocks while loading.
         */
        fun create(context: Context): WhisperRecognizer {
            val config = SttModelManager(context).getActiveConfig()
            if (config == null) {
                DebugLog.e("STT", "No STT model available — recognizer not initialized")
                return WhisperRecognizer(null)
            }
            synchronized(lock) {
                current?.takeIf { it.id == config.id }?.let {
                    it.refs += 1
                    DebugLog.i("STT", "Recognizer shared: model=${it.id} refs=${it.refs}")
                    return WhisperRecognizer(it)
                }
                val loaded = load(context, config) ?: return WhisperRecognizer(null)
                loaded.refs = 1
                current = loaded
                return WhisperRecognizer(loaded)
            }
        }

        private fun unref(m: Model) {
            synchronized(lock) {
                m.refs -= 1
                if (m.refs > 0) return
                if (current === m) current = null
            }
            DebugLog.i("STT", "Recognizer released: model=${m.id}")
            m.free()
        }

        private fun load(context: Context, config: SttModelConfig): Model? {
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
            return recognizer?.let { Model(config.id, it, provider) }
        }
    }
}
