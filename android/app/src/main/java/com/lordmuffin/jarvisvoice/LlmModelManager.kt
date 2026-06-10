package com.lordmuffin.jarvisvoice

import android.content.Context
import java.io.File

class LlmModelManager(private val context: Context) {

    companion object {
        private const val PREF_FILE = "jarvis_llm_models"
        private const val KEY_ACTIVE_MODEL = "active_model"
    }

    fun modelsDir(): File = PersistentStorage.llmModelsDir(context)

    fun modelFile(config: ModelConfig): File = File(modelsDir(), config.filename)

    fun isInstalled(config: ModelConfig): Boolean = modelFile(config).let { it.exists() && it.length() > 0 }

    fun installedSizeMb(config: ModelConfig): Long =
        if (isInstalled(config)) modelFile(config).length() / (1024 * 1024) else 0L

    fun getActiveModelId(): String =
        context.getSharedPreferences(PREF_FILE, Context.MODE_PRIVATE)
            .getString(KEY_ACTIVE_MODEL, ModelRegistry.NO_LLM) ?: ModelRegistry.NO_LLM

    fun getActiveConfig(): ModelConfig? {
        val id = getActiveModelId()
        return ModelRegistry.MODELS.find { it.id == id }
    }

    fun setActiveModel(id: String) {
        context.getSharedPreferences(PREF_FILE, Context.MODE_PRIVATE)
            .edit().putString(KEY_ACTIVE_MODEL, id).apply()
        DebugLog.i("LlmModelManager", "active model set to $id")
    }

    fun deleteModel(config: ModelConfig) {
        modelFile(config).delete()
        if (getActiveModelId() == config.id) setActiveModel(ModelRegistry.NO_LLM)
        DebugLog.i("LlmModelManager", "deleted ${config.id}")
    }

    fun getStatusLabel(config: ModelConfig): String = when {
        isInstalled(config) -> "Installed · ${installedSizeMb(config)} MB"
        else                -> "${config.fileSizeMb} MB download"
    }
}
