package com.lordmuffin.jarvisvoice.live

import android.content.Context
import android.os.Build
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.io.File
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * Jarvis Live connection settings. The server URL lives in `jarvis_prefs`; the device token
 * (from `jarvis-live create-device`) is encrypted with an Android Keystore key, the counterpart of
 * the Mac app's Keychain item.
 */
class LiveSettings(private val context: Context) {
    private val prefs = context.getSharedPreferences("jarvis_prefs", Context.MODE_PRIVATE)

    var serverUrl: String
        get() = prefs.getString(KEY_SERVER_URL, null)?.takeIf { it.isNotBlank() } ?: DEFAULT_SERVER_URL
        set(value) = prefs.edit().putString(KEY_SERVER_URL, value.trim()).apply()

    var deviceToken: String?
        get() = prefs.getString(KEY_TOKEN, null)?.let { SecretStore.decrypt(it) }
        set(value) {
            if (value.isNullOrBlank()) prefs.edit().remove(KEY_TOKEN).apply()
            else prefs.edit().putString(KEY_TOKEN, SecretStore.encrypt(value.trim())).apply()
        }

    val isConfigured: Boolean get() = !deviceToken.isNullOrBlank()

    /** `hello.device`: free-form on the wire; the server only logs it. */
    val deviceName: String get() = "android:${Build.MODEL}"

    fun apiClient(): LiveApiClient = LiveApiClient(serverUrl, deviceToken.orEmpty())

    /** `filesDir/live/sessions`: outboxes and session metadata. */
    val sessionsRoot: File get() = File(context.filesDir, "live/sessions").also { it.mkdirs() }

    companion object {
        const val DEFAULT_SERVER_URL = "https://live.lab.apj.dev"
        private const val KEY_SERVER_URL = "live_server_url"
        private const val KEY_TOKEN = "live_device_token_enc"
    }
}

/** AES-GCM with a non-exportable Android Keystore key. */
private object SecretStore {
    private const val ALIAS = "jarvis_live_token"
    private const val TRANSFORMATION = "AES/GCM/NoPadding"

    fun encrypt(plain: String): String {
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.ENCRYPT_MODE, key())
        val out = cipher.iv + cipher.doFinal(plain.toByteArray())
        return Base64.encodeToString(out, Base64.NO_WRAP)
    }

    fun decrypt(encoded: String): String? = runCatching {
        val bytes = Base64.decode(encoded, Base64.NO_WRAP)
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, bytes, 0, 12))
        String(cipher.doFinal(bytes, 12, bytes.size - 12))
    }.getOrNull()

    private fun key(): SecretKey {
        val ks = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (ks.getKey(ALIAS, null) as? SecretKey)?.let { return it }
        val gen = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        gen.init(
            KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .build()
        )
        return gen.generateKey()
    }
}
