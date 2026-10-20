package com.lordmuffin.jarvisvoice.live

import okhttp3.HttpUrl.Companion.toHttpUrl

/** Pure helpers for the Pocket-ID browser handoff (see `server/src/jarvis_live/api/auth.py`). */
object SignIn {
    /** The fixed redirect the server sends the browser to; must match the manifest intent-filter. */
    const val CALLBACK_URI = "com.lordmuffin.jarvisvoice://auth/callback"

    /** How long a started sign-in waits for its callback (the server's flow lives 10 minutes). */
    const val PENDING_TTL_MS = 15 * 60 * 1000L

    /** `{server}/auth/login?client=android&challenge=<challenge>` */
    fun loginUrl(serverUrl: String, challenge: String): String =
        serverUrl.trim().trimEnd('/').toHttpUrl().newBuilder()
            .addPathSegments("auth/login")
            .addQueryParameter("client", "android")
            .addQueryParameter("challenge", challenge)
            .build()
            .toString()

    sealed class Callback {
        data class Code(val code: String) : Callback()
        data class Error(val error: String) : Callback()
    }

    /** Interprets the callback's `code` / `error` query parameters. */
    fun callback(code: String?, error: String?): Callback = when {
        !error.isNullOrBlank() -> Callback.Error(error)
        !code.isNullOrBlank() -> Callback.Code(code)
        else -> Callback.Error("cancelled")
    }

    /** `device_name` for the new token: "Google Pixel 8", not "Google Google Pixel 8". */
    fun deviceLabel(manufacturer: String?, model: String?): String {
        val m = model?.trim().orEmpty()
        val brand = manufacturer?.trim().orEmpty().replaceFirstChar { it.uppercaseChar() }
        val label = when {
            m.isEmpty() -> brand
            brand.isEmpty() || m.startsWith(brand, ignoreCase = true) -> m
            else -> "$brand $m"
        }
        return label.ifBlank { "Android" }.take(200)
    }
}
