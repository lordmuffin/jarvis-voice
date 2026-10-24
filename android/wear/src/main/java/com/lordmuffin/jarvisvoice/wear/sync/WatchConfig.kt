package com.lordmuffin.jarvisvoice.wear.sync

import android.content.Context
import com.lordmuffin.jarvisvoice.wearcommon.SecretStore

/** The server URL and device token the phone pushed over the Data Layer, token encrypted at rest. */
class WatchConfig(context: Context) {
    data class Server(val serverUrl: String, val token: String)

    private val prefs = context.getSharedPreferences("jarvis_wear_prefs", Context.MODE_PRIVATE)

    fun current(): Server? {
        val url = prefs.getString(KEY_URL, null)?.takeIf { it.isNotBlank() } ?: return null
        val token = prefs.getString(KEY_TOKEN, null)?.let { SecretStore.decrypt(it) }?.takeIf { it.isNotBlank() } ?: return null
        return Server(url, token)
    }

    val isConfigured get() = current() != null

    /** Returns true if this differs from what was stored. */
    fun update(serverUrl: String?, token: String?): Boolean {
        val before = current()
        if (serverUrl.isNullOrBlank() || token.isNullOrBlank()) {
            prefs.edit().remove(KEY_URL).remove(KEY_TOKEN).apply()
        } else {
            prefs.edit().putString(KEY_URL, serverUrl.trim()).putString(KEY_TOKEN, SecretStore.encrypt(token.trim())).apply()
        }
        return before != current()
    }

    private companion object {
        const val KEY_URL = "server_url"
        const val KEY_TOKEN = "token_enc"
    }
}
