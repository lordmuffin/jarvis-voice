package com.lordmuffin.jarvisvoice

import android.content.Context
import com.lordmuffin.jarvisvoice.speech.SttModelConfig

object ModelServerConfig {

    const val KEY_LLM_BASE_URL = "custom_llm_base_url"
    const val KEY_STT_BASE_URL = "custom_stt_base_url"
    const val KEY_SERVER_TOKEN = "custom_server_token"

    private fun prefs(context: Context) =
        context.getSharedPreferences(VoiceOverlayService.PREF_FILE, Context.MODE_PRIVATE)

    fun getLlmBaseUrl(context: Context): String =
        prefs(context).getString(KEY_LLM_BASE_URL, "")?.trim() ?: ""

    fun getSttBaseUrl(context: Context): String =
        prefs(context).getString(KEY_STT_BASE_URL, "")?.trim() ?: ""

    fun getServerToken(context: Context): String? =
        prefs(context).getString(KEY_SERVER_TOKEN, "")?.trim()?.ifEmpty { null }

    /**
     * Returns (downloadUrl, authorizationHeader) for an LLM model.
     * Custom URL + token take precedence; falls back to HuggingFace + HF token.
     */
    fun resolveLlm(context: Context, config: ModelConfig): Pair<String, String?> {
        val base = getLlmBaseUrl(context)
        return if (base.isNotEmpty()) {
            val url = "${base.trimEnd('/')}/${config.filename}"
            url to getServerToken(context)?.let { "Bearer $it" }
        } else {
            val hfToken = prefs(context).getString("hf_token", null)?.trim()?.ifEmpty { null }
            config.downloadUrl to hfToken?.let { "Bearer $it" }
        }
    }

    /**
     * Returns (downloadUrl, authorizationHeader) for an STT model tar archive.
     * Custom URL + token take precedence; falls back to the GitHub release URL (no auth).
     */
    fun resolveStt(context: Context, config: SttModelConfig): Pair<String, String?> {
        val base = getSttBaseUrl(context)
        return if (base.isNotEmpty()) {
            val tarFilename = config.tarUrl.substringAfterLast('/')
            val url = "${base.trimEnd('/')}/$tarFilename"
            url to getServerToken(context)?.let { "Bearer $it" }
        } else {
            config.tarUrl to null
        }
    }

    fun isCustomLlmConfigured(context: Context) = getLlmBaseUrl(context).isNotEmpty()
    fun isCustomSttConfigured(context: Context) = getSttBaseUrl(context).isNotEmpty()
}
