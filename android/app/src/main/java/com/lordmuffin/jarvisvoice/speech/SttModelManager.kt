package com.lordmuffin.jarvisvoice.speech

import android.content.Context
import com.lordmuffin.jarvisvoice.PersistentStorage
import java.io.File

class SttModelManager(private val context: Context) {

    private val prefs = context.getSharedPreferences(
        SpeechEngineFactory.PREF_FILE, Context.MODE_PRIVATE
    )

    fun getActiveModelId(): String =
        prefs.getString(SttModelRegistry.PREF_ACTIVE_MODEL, SttModelRegistry.DEFAULT_MODEL_ID)
            ?: SttModelRegistry.DEFAULT_MODEL_ID

    fun setActiveModel(id: String) {
        prefs.edit().putString(SttModelRegistry.PREF_ACTIVE_MODEL, id).apply()
    }

    fun modelDir(config: SttModelConfig): File =
        PersistentStorage.sttModelDir(context, config.subdir)

    fun isInstalled(config: SttModelConfig): Boolean {
        val dir = modelDir(config)
        return listOf(config.encoderFile, config.decoderFile, config.tokensFile)
            .all { File(dir, it).let { f -> f.exists() && f.length() > 0 } }
    }

    fun getActiveConfig(): SttModelConfig? {
        val id = getActiveModelId()
        val preferred = SttModelRegistry.MODELS.find { it.id == id }
        if (preferred != null && isInstalled(preferred)) return preferred
        return SttModelRegistry.MODELS.firstOrNull { isInstalled(it) }
    }

    fun deleteModel(config: SttModelConfig) {
        modelDir(config).deleteRecursively()
        if (getActiveModelId() == config.id) {
            val fallback = SttModelRegistry.MODELS.firstOrNull { it.id != config.id && isInstalled(it) }
            prefs.edit().putString(
                SttModelRegistry.PREF_ACTIVE_MODEL,
                fallback?.id ?: SttModelRegistry.DEFAULT_MODEL_ID
            ).apply()
        }
    }

    fun getStatusLabel(config: SttModelConfig): String =
        if (getActiveModelId() == config.id && isInstalled(config)) "Active" else "Installed"
}
