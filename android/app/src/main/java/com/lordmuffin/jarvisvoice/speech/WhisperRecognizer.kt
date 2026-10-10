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
 * The native model is loaded once per process and provider preference, and reference-counted: the
 * overlay service keeps dictation's handle for its whole lifetime, so callers with the same
 * preference share one copy instead of holding two. Native calls are serialized, and the model is
 * freed only after the last handle is released and any in-flight transcription has returned.
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
    private class Model(
        val id: String,
        /** Model id plus provider preference: the cache key in [loaded]. */
        val key: String,
        private var recognizer: OfflineRecognizer?,
        val provider: String,
    ) {
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
        /**
         * Loaded models by [Model.key]. A replaced model (another active model chosen) lives on
         * until its last handle is released.
         */
        private val loaded = HashMap<String, Model>()

        /**
         * Returns a handle on the active model, loading it unless it is already loaded with the
         * same provider preference: NNAPI first, then CPU; or CPU only when [cpuOnly]. The handle
         * must be [release]d. Blocks while loading.
         */
        fun create(context: Context, cpuOnly: Boolean = false): WhisperRecognizer {
            val config = SttModelManager(context).getActiveConfig()
            if (config == null) {
                DebugLog.e("STT", "No STT model available — recognizer not initialized")
                return WhisperRecognizer(null)
            }
            val key = "${config.id}/${if (cpuOnly) "cpu" else "auto"}"
            synchronized(lock) {
                loaded[key]?.let {
                    it.refs += 1
                    DebugLog.i("STT", "Recognizer shared: model=${it.id} provider=${it.provider} refs=${it.refs}")
                    return WhisperRecognizer(it)
                }
                val model = load(context, config, key, cpuOnly) ?: return WhisperRecognizer(null)
                model.refs = 1
                loaded[key] = model
                return WhisperRecognizer(model)
            }
        }

        private fun unref(m: Model) {
            synchronized(lock) {
                m.refs -= 1
                if (m.refs > 0) return
                if (loaded[m.key] === m) loaded.remove(m.key)
            }
            DebugLog.i("STT", "Recognizer released: model=${m.id}")
            m.free()
        }

        private fun load(context: Context, config: SttModelConfig, key: String, cpuOnly: Boolean): Model? {
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
            var recognizer: OfflineRecognizer? = null
            if (!cpuOnly) {
                recognizer = runCatching { OfflineRecognizer(config = cfg("nnapi")) }
                    .onFailure { DebugLog.e("STT", "nnapi backend failed", it) }
                    .getOrNull()
            }
            if (recognizer == null) {
                provider = "cpu"
                recognizer = runCatching { OfflineRecognizer(config = cfg("cpu")) }
                    .onFailure { DebugLog.e("STT", "cpu backend also failed", it) }
                    .getOrNull()
            }
            DebugLog.i("STT", "Recognizer init: ${if (recognizer != null) "OK" else "FAILED"} provider=$provider model=${config.id}")
            return recognizer?.let { Model(config.id, key, it, provider) }
        }
    }
}
