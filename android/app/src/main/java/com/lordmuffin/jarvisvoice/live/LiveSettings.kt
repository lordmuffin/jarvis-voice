package com.lordmuffin.jarvisvoice.live

import android.content.Context
import android.content.SharedPreferences
import android.os.Build
import com.lordmuffin.jarvisvoice.watch.WearBridge
import com.lordmuffin.jarvisvoice.wearcommon.SecretStore
import java.io.File

/**
 * Jarvis Live connection settings. The server URL lives in `jarvis_prefs`; the device token
 * (from Pocket-ID sign-in, or pasted from the dashboard's Devices page) is encrypted with an Android Keystore key, the counterpart of
 * the Mac app's Keychain item.
 */
class LiveSettings(private val context: Context) {
    private val prefs = context.getSharedPreferences("jarvis_prefs", Context.MODE_PRIVATE)

    var serverUrl: String
        get() = prefs.getString(KEY_SERVER_URL, null)?.takeIf { it.isNotBlank() } ?: DEFAULT_SERVER_URL
        set(value) {
            prefs.edit().putString(KEY_SERVER_URL, value.trim()).apply()
            WearBridge.publishConfigAsync(context)
        }

    var deviceToken: String?
        get() = prefs.getString(KEY_TOKEN, null)?.let { SecretStore.decrypt(it) }
        set(value) {
            if (value.isNullOrBlank()) prefs.edit().remove(KEY_TOKEN).apply()
            else prefs.edit().putString(KEY_TOKEN, SecretStore.encrypt(value.trim())).apply()
            // The watch uploads with this token too, so it follows sign-in and sign-out.
            WearBridge.publishConfigAsync(context)
        }

    /** Display name from `GET /v1/me`, cached so Settings can show it before the request returns. */
    var accountName: String?
        get() = prefs.getString(KEY_ACCOUNT_NAME, null)?.takeIf { it.isNotBlank() }
        set(value) {
            if (value.isNullOrBlank()) prefs.edit().remove(KEY_ACCOUNT_NAME).apply()
            else prefs.edit().putString(KEY_ACCOUNT_NAME, value).apply()
        }

    /**
     * The Record tab's Local only switch and what drives it. The last network check is kept too,
     * so the switch starts from where the phone last was while the next check runs.
     */
    var awayMode: AutoLocalOnly
        get() = AutoLocalOnly(
            enabled = prefs.getBoolean(KEY_AWAY_ENABLED, true),
            place = NetworkPlace.fromName(prefs.getString(KEY_AWAY_PLACE, null)),
            manual = prefs.getTriState(KEY_AWAY_MANUAL),
            manualAgainst = prefs.getTriState(KEY_AWAY_AGAINST),
        )
        set(value) {
            prefs.edit()
                .putBoolean(KEY_AWAY_ENABLED, value.enabled)
                .putString(KEY_AWAY_PLACE, value.place?.name)
                .putTriState(KEY_AWAY_MANUAL, value.manual)
                .putTriState(KEY_AWAY_AGAINST, value.manualAgainst)
                .apply()
        }

    private fun SharedPreferences.getTriState(key: String): Boolean? =
        if (contains(key)) getBoolean(key, false) else null

    private fun SharedPreferences.Editor.putTriState(key: String, value: Boolean?) =
        if (value == null) remove(key) else putBoolean(key, value)

    /** A Pocket-ID sign-in waiting for its browser callback. */
    data class PendingSignIn(val verifier: String, val serverUrl: String, val startedAt: Long)

    /**
     * Starts a sign-in against [serverUrl]: stores a fresh PKCE verifier (encrypted, in prefs, so it
     * survives the activity or process being recreated while the browser is up) and returns the
     * login URL to open.
     */
    fun beginSignIn(serverUrl: String, now: Long = System.currentTimeMillis()): String {
        val verifier = Pkce.newVerifier()
        prefs.edit()
            .putString(KEY_PENDING_VERIFIER, SecretStore.encrypt(verifier))
            .putString(KEY_PENDING_URL, serverUrl)
            .putLong(KEY_PENDING_AT, now)
            .apply()
        return SignIn.loginUrl(serverUrl, Pkce.challenge(verifier))
    }

    /** The sign-in in progress, or null if there is none or it is older than [SignIn.PENDING_TTL_MS]. */
    fun pendingSignIn(now: Long = System.currentTimeMillis()): PendingSignIn? {
        val verifier = prefs.getString(KEY_PENDING_VERIFIER, null)?.let { SecretStore.decrypt(it) } ?: return null
        val url = prefs.getString(KEY_PENDING_URL, null) ?: return null
        val at = prefs.getLong(KEY_PENDING_AT, 0)
        if (now - at !in 0..SignIn.PENDING_TTL_MS) return null
        return PendingSignIn(verifier, url, at)
    }

    fun clearPendingSignIn() {
        prefs.edit().remove(KEY_PENDING_VERIFIER).remove(KEY_PENDING_URL).remove(KEY_PENDING_AT).apply()
    }

    val isConfigured: Boolean get() = !deviceToken.isNullOrBlank()

    /** `hello.device`: free-form on the wire; the server only logs it. */
    val deviceName: String get() = "android:${Build.MODEL}"

    fun apiClient(): LiveApiClient = LiveApiClient(serverUrl, deviceToken.orEmpty())

    /** `filesDir/live/sessions`: outboxes and session metadata. */
    val sessionsRoot: File get() = File(context.filesDir, "live/sessions").also { it.mkdirs() }

    companion object {
        const val DEFAULT_SERVER_URL = "https://live.apj.dev"
        private const val KEY_SERVER_URL = "live_server_url"
        private const val KEY_TOKEN = "live_device_token_enc"
        private const val KEY_ACCOUNT_NAME = "live_account_name"
        private const val KEY_PENDING_VERIFIER = "live_signin_verifier_enc"
        private const val KEY_PENDING_URL = "live_signin_server_url"
        private const val KEY_PENDING_AT = "live_signin_started_at"
        private const val KEY_AWAY_ENABLED = "live_away_local_only"
        private const val KEY_AWAY_PLACE = "live_away_place"
        private const val KEY_AWAY_MANUAL = "live_away_manual"
        private const val KEY_AWAY_AGAINST = "live_away_manual_against"
    }
}
