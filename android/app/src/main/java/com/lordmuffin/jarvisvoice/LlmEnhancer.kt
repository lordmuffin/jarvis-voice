package com.lordmuffin.jarvisvoice

import com.google.ai.edge.litertlm.Backend
import com.google.ai.edge.litertlm.Engine
import com.google.ai.edge.litertlm.EngineConfig
import java.io.File
import kotlinx.coroutines.runBlocking

/**
 * On-device LLM transcript enhancement via LiteRT-LM (litertlm-android:0.13.1).
 *
 * init() runs on a background thread (callers' responsibility).
 * enhance() is blocking — callers must NOT call from the main thread.
 * App degrades gracefully to raw transcript if no model is loaded.
 */
object LlmEnhancer {

    private var engine: Engine? = null
    private var loadedModelId: String? = null

    fun isReady(): Boolean = engine != null

    fun init(modelFile: File, modelId: String): Boolean {
        if (loadedModelId == modelId && isReady()) return true
        destroy()

        return try {
            val config = EngineConfig(
                modelPath = modelFile.absolutePath,
                backend = Backend.CPU()
            )
            val e = Engine(config)
            runBlocking { e.initialize() }
            engine = e
            loadedModelId = modelId
            DebugLog.i("LlmEnhancer", "loaded $modelId from ${modelFile.absolutePath}")
            true
        } catch (e: Exception) {
            DebugLog.e("LlmEnhancer", "failed to load $modelId", e)
            false
        }
    }

    /** Blocking. Must be called from a background thread. Returns rawTranscript on any failure. */
    fun enhance(rawTranscript: String): String {
        val e = engine ?: return rawTranscript

        val prompt = "Fix this voice dictation transcript. Clean up grammar, fix punctuation, " +
            "and expand obvious abbreviations. Return only the corrected text — no explanations, " +
            "no prefixes.\n\nTranscript: $rawTranscript\n\nCorrected:"

        return try {
            e.createConversation().use { conv ->
                val response = runBlocking { conv.sendMessage(prompt) }
                response.toString().trim().ifBlank { rawTranscript }
            }
        } catch (ex: Exception) {
            DebugLog.e("LlmEnhancer", "enhance failed", ex)
            rawTranscript
        }
    }

    fun destroy() {
        try { engine?.close() } catch (_: Exception) {}
        engine = null
        loadedModelId = null
    }
}
