package com.lordmuffin.jarvisvoice

import android.os.Build
import com.google.mlkit.genai.common.DownloadStatus
import com.google.mlkit.genai.common.FeatureStatus
import com.google.mlkit.genai.prompt.Generation
import com.google.mlkit.genai.prompt.GenerativeModel
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.runBlocking

/**
 * On-device LLM enhancement via Android AI Core (Gemini Nano).
 *
 * Uses the ML Kit GenAI Prompt API to run Gemini Nano through the OS-managed AI Core service.
 * No model file — the model is installed and managed by the system.
 *
 * Requires Android 14+ (API 34). Returns the raw transcript unchanged on any failure.
 * initialize() and enhance() are blocking — call from a background thread.
 */
object AiCoreEnhancer {

    private var model: GenerativeModel? = null
    @Volatile private var ready = false

    // Last status from checkStatus() — exposed so the UI can show Download/Ready/Unavailable.
    // -1 = not checked yet, otherwise a FeatureStatus int constant.
    @Volatile var lastStatus: Int = -1
        private set

    fun isReady(): Boolean = ready && model != null

    fun initialize(): Boolean {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            DebugLog.i("AiCore", "Android 14+ required (SDK=${Build.VERSION.SDK_INT})")
            return false
        }
        return try {
            // Re-use existing model object when already created (e.g. after download completes).
            val m = model ?: Generation.getClient().also { model = it }
            val status = runBlocking { m.checkStatus() }
            lastStatus = status
            when (status) {
                FeatureStatus.AVAILABLE -> {
                    ready = true
                    DebugLog.i("AiCore", "Gemini Nano ready")
                    true
                }
                FeatureStatus.UNAVAILABLE -> {
                    DebugLog.w("AiCore", "Gemini Nano unavailable on this device")
                    ready = false
                    try { m.close() } catch (_: Exception) {}
                    model = null
                    false
                }
                else -> {
                    // DOWNLOADABLE or DOWNLOADING — keep model alive so downloadFlow() works.
                    DebugLog.i("AiCore", "Gemini Nano not yet downloaded (status=$status)")
                    ready = false
                    false
                }
            }
        } catch (e: Exception) {
            DebugLog.e("AiCore", "initialization failed", e)
            false
        }
    }

    /**
     * Returns a Flow that drives the Gemini Nano download. Collect on a coroutine scope.
     * After DownloadCompleted, call initialize() again — status will be AVAILABLE.
     */
    fun downloadFlow(): Flow<DownloadStatus>? {
        return try {
            val m = model ?: Generation.getClient().also { model = it }
            m.download()
        } catch (e: Exception) {
            DebugLog.e("AiCore", "downloadFlow() failed", e)
            null
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
            // BACKGROUND_USE_BLOCKED (ErrorCode 30) is expected when the overlay runs over
            // another app — log as debug since the raw transcript is a valid fallback.
            if (e.message?.contains("BACKGROUND_USE_BLOCKED") == true) {
                DebugLog.i("AiCore", "enhance skipped — foreground required (BACKGROUND_USE_BLOCKED)")
            } else {
                DebugLog.e("AiCore", "enhance failed", e)
            }
            rawTranscript
        }
    }

    fun destroy() {
        try { model?.close() } catch (_: Exception) {}
        model = null
        ready = false
        lastStatus = -1
    }
}
