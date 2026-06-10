package com.lordmuffin.jarvisvoice

import android.content.Context
import java.io.File

/**
 * On-device LLM transcript enhancement via MediaPipe Tasks GenAI.
 *
 * The inference backend uses com.google.mediapipe:tasks-genai.
 * If the model isn't installed or inference fails, the raw transcript is returned unchanged.
 *
 * Update MEDIAPIPE_VERSION in build.gradle.kts if the SDK API changes.
 */
object LlmEnhancer {

    private var loadedModelId: String? = null

    // MediaPipe LlmInference is loaded reflectively to avoid hard compile dependency
    // when the MediaPipe AAR is not on the classpath (e.g. CI --no-model builds).
    // Replace with a direct import once the SDK version is confirmed stable.
    private var inference: Any? = null
    private var generateResponseMethod: java.lang.reflect.Method? = null

    fun isReady(): Boolean = inference != null

    fun init(context: Context, modelFile: File, modelId: String): Boolean {
        if (loadedModelId == modelId && isReady()) return true
        destroy()

        return try {
            val optionsClass = Class.forName(
                "com.google.mediapipe.tasks.genai.llminference.LlmInference\$LlmInferenceOptions"
            )
            val builderClass = Class.forName(
                "com.google.mediapipe.tasks.genai.llminference.LlmInference\$LlmInferenceOptions\$Builder"
            )
            val builder = builderClass.newInstance()
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
            inference?.let {
                it.javaClass.getMethod("close").invoke(it)
            }
        } catch (_: Exception) {}
        inference = null
        generateResponseMethod = null
        loadedModelId = null
    }
}
