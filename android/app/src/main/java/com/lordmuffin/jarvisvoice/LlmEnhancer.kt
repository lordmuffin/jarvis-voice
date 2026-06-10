package com.lordmuffin.jarvisvoice

import android.content.Context
import java.io.File

/**
 * On-device LLM transcript enhancement via MediaPipe Tasks GenAI (tasks-genai:0.10.24+).
 *
 * Loaded reflectively so the app keeps working if the model isn't installed.
 * init() runs on a background thread (callers' responsibility).
 * enhance() is blocking — callers must NOT call from the main thread.
 */
object LlmEnhancer {

    private var loadedModelId: String? = null

    // Reflective handles — populated on successful init(), nulled on destroy().
    private var inference: Any? = null
    private var generateResponseMethod: java.lang.reflect.Method? = null

    fun isReady(): Boolean = inference != null

    fun init(context: Context, modelFile: File, modelId: String): Boolean {
        if (loadedModelId == modelId && isReady()) return true
        destroy()

        return try {
            // LlmInferenceOptions is a top-level class (not nested inside LlmInference).
            // Its static builder() method returns LlmInferenceOptions.Builder.
            val optionsClass = Class.forName(
                "com.google.mediapipe.tasks.genai.llminference.LlmInferenceOptions"
            )
            val builder = optionsClass.getMethod("builder").invoke(null)!!
            val builderClass = builder.javaClass
            builderClass.getMethod("setModelPath", String::class.java)
                .invoke(builder, modelFile.absolutePath)
            builderClass.getMethod("setMaxTokens", Int::class.java)
                .invoke(builder, 512)
            val options = builderClass.getMethod("build").invoke(builder)

            val llmClass = Class.forName(
                "com.google.mediapipe.tasks.genai.llminference.LlmInference"
            )
            val createMethod = llmClass.getMethod("createFromOptions", Context::class.java, optionsClass)
            inference = createMethod.invoke(null, context, options)
            generateResponseMethod = llmClass.getMethod("generateResponse", String::class.java)
            loadedModelId = modelId
            DebugLog.i("LlmEnhancer", "loaded model: $modelId from ${modelFile.absolutePath}")
            true
        } catch (e: Exception) {
            DebugLog.e("LlmEnhancer", "failed to load $modelId", e)
            false
        }
    }

    /** Blocking. Must be called from a background thread. Returns rawTranscript on any failure. */
    fun enhance(rawTranscript: String): String {
        val llm    = inference ?: return rawTranscript
        val method = generateResponseMethod ?: return rawTranscript

        val prompt = """Fix this voice dictation transcript. Clean up grammar, fix punctuation, and expand obvious abbreviations. Return only the corrected text — no explanations, no prefixes.

Transcript: $rawTranscript

Corrected:"""
        return try {
            (method.invoke(llm, prompt) as? String)?.trim()?.ifBlank { rawTranscript }
                ?: rawTranscript
        } catch (e: Exception) {
            DebugLog.e("LlmEnhancer", "enhance failed", e)
            rawTranscript
        }
    }

    fun destroy() {
        try {
            inference?.let { it.javaClass.getMethod("close").invoke(it) }
        } catch (_: Exception) {}
        inference = null
        generateResponseMethod = null
        loadedModelId = null
    }
}
