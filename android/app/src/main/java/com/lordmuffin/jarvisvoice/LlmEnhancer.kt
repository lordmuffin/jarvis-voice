package com.lordmuffin.jarvisvoice

import com.google.ai.edge.litertlm.Backend
import com.google.ai.edge.litertlm.Engine
import com.google.ai.edge.litertlm.EngineConfig
import java.io.File
import kotlinx.coroutines.runBlocking

/**
 * On-device LLM transcript enhancement via LiteRT-LM (litertlm-android:0.13.1).
 *
 * Hardware acceleration priority: NPU → GPU → CPU
 *   NPU  — Google Tensor G5 via system Tensor SDK (Pixel 9+). No bundled .so needed;
 *           LiteRT discovers the plugin through the system library path.
 *   GPU  — OpenCL/OpenGL via libLiteRtClGlAccelerator.so (bundled in the AAR).
 *   CPU  — Always available fallback.
 *
 * init() runs on a background thread (callers' responsibility).
 * enhance() is blocking — callers must NOT call from the main thread.
 */
object LlmEnhancer {

    private var engine: Engine? = null
    private var loadedModelId: String? = null
    var activeBackend: String = "none"
        private set

    fun isReady(): Boolean = engine != null

    fun init(modelFile: File, modelId: String, nativeLibraryDir: String = ""): Boolean {
        if (loadedModelId == modelId && isReady()) return true
        destroy()

        // Try each backend in priority order; first one that loads wins.
        val candidates = listOf(
            "npu"  to { Backend.NPU(nativeLibraryDir) },
            "gpu"  to { Backend.GPU() },
            "cpu"  to { Backend.CPU() },
        )

        for ((label, makeBackend) in candidates) {
            val result = runCatching {
                val config = EngineConfig(
                    modelPath = modelFile.absolutePath,
                    backend   = makeBackend()
                )
                val e = Engine(config)
                runBlocking { e.initialize() }
                e
            }
            if (result.isSuccess) {
                engine          = result.getOrThrow()
                loadedModelId   = modelId
                activeBackend   = label
                DebugLog.i("LlmEnhancer", "loaded $modelId via $label backend")
                return true
            } else {
                DebugLog.i("LlmEnhancer", "$label backend unavailable — ${result.exceptionOrNull()?.message}")
            }
        }

        DebugLog.e("LlmEnhancer", "all backends failed for $modelId")
        return false
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
        } catch (t: Throwable) {
            DebugLog.e("LlmEnhancer", "enhance failed", t)
            rawTranscript
        }
    }

    fun destroy() {
        try { engine?.close() } catch (_: Exception) {}
        engine        = null
        loadedModelId = null
        activeBackend = "none"
    }
}
