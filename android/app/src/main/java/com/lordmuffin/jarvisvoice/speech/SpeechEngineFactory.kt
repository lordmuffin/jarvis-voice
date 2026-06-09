package com.lordmuffin.jarvisvoice.speech

import android.content.Context

object SpeechEngineFactory {
    const val PREF_FILE = "jarvis_prefs"
    const val KEY_OFFLINE_STT = "offline_stt"

    fun create(context: Context): SpeechEngine {
        return if (isOfflineModeEnabled(context) && SherpaOnnxSpeechEngine.isModelAvailable(context)) {
            SherpaOnnxSpeechEngine(context)
        } else {
            AndroidSpeechEngine(context)
        }
    }

    fun isOfflineModeEnabled(context: Context): Boolean =
        context.getSharedPreferences(PREF_FILE, Context.MODE_PRIVATE)
            .getBoolean(KEY_OFFLINE_STT, true)

    fun setOfflineMode(context: Context, enabled: Boolean) {
        context.getSharedPreferences(PREF_FILE, Context.MODE_PRIVATE)
            .edit().putBoolean(KEY_OFFLINE_STT, enabled).apply()
    }
}
