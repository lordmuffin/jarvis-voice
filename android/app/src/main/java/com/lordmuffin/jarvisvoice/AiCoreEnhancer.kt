package com.lordmuffin.jarvisvoice

import android.os.Build
import com.google.mlkit.genai.common.FeatureStatus
import com.google.mlkit.genai.prompt.Generation
import com.google.mlkit.genai.prompt.GenerativeModel
import kotlinx.coroutines.runBlocking

/**
 * On-device LLM enhancement via Android AI Core (Gemini Nano).
 *
 * Uses the ML Kit GenAI Prompt API to run Gemini Nano through the OS-managed AI Core service.
 * No model file download — the model is installed and managed by the system.
 *
 * Requires Android 14+ (API 34) and a compatible device (Pixel 8+ / other AI Core-capable hardware).
 * Returns the raw transcript unchanged on any failure so the caller always gets usable output.
 *
 * initialize() and enhance() are both blocking — call from a background thread.
 */
object AiCoreEnhancer {

    private var model: GenerativeModel? = null
    @Volatile private var ready = false

    fun isReady(): Boolean = ready && model != null

    fun initialize(): Boolean {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            DebugLog.i("AiCore", "Android 14+ required (SDK=${Build.VERSION.SDK_INT})")
            return false
        }
        return try {
            val m = Generation.getClient()
            val status = runBlocking { m.checkStatus() }
            when (status) {
                FeatureStatus.AVAILABLE -> {
                    model = m
                    ready = true
                    DebugLog.i("AiCore", "Gemini Nano ready")
                    true
                }
                FeatureStatus.UNAVAILABLE -> {
                    DebugLog.w("AiCore", "Gemini Nano unavailable on this device")
                    try { m.close() } catch (_: Exception) {}
                    false
                }
                else -> {
                    DebugLog.i("AiCore", "Gemini Nano not yet downloaded (status=$status) — open device AI settings to download")
                    try { m.close() } catch (_: Exception) {}
                    false
                }
            }
        } catch (e: Exception) {
            DebugLog.e("AiCore", "initialization failed", e)
            false
        }
    }

    fun enhance(rawTranscript: String): String {
        val m = model ?: return rawTranscript
        val prompt = "Fix this voice dictation transcript. Clean up grammar, fix punctuation, " +
            "and expand obvious abbreviations. Return only the corrected text — no explanations, " +
            "no prefixes.\n\nTranscript: $rawTranscript\n\nCorrected:"
        return try {
            val response = runBlocking { m.generateContent(prompt) }
            response.candidates.firstOrNull()?.text?.trim()?.ifBlank { rawTranscript } ?: rawTranscript
        } catch (e: Exception) {
            DebugLog.e("AiCore", "enhance failed", e)
            rawTranscript
        }
    }

    fun destroy() {
        try { model?.close() } catch (_: Exception) {}
        model = null
        ready = false
    }
}
