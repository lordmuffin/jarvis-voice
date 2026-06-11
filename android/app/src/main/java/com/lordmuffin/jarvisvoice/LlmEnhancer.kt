package com.lordmuffin.jarvisvoice

import android.content.Context
import android.content.SharedPreferences
import com.google.ai.edge.litertlm.Backend
import com.google.ai.edge.litertlm.Engine
import com.google.ai.edge.litertlm.EngineConfig
import java.io.File
import kotlinx.coroutines.runBlocking

/**
 * On-device LLM transcript enhancement via LiteRT-LM (litertlm-android:0.13.1).
 *
 * Hardware acceleration priority: NPU → GPU → CPU
 *   NPU  — Google Tensor G5 via system Tensor SDK (Pixel 9+).
 *   GPU  — OpenCL/OpenGL via libLiteRtClGlAccelerator.so (bundled in the AAR).
 *   CPU  — Always available fallback.
 *
 * Crash sentinel: a SharedPreferences key is written before each NPU/GPU init attempt
 * and cleared on any outcome we can observe (success or caught exception). If the
 * process is killed by an uncatchable native abort during init, the key survives and
 * the next startup skips hardware backends to avoid a crash loop.
 *
 * init() runs on a background thread (callers' responsibility).
 * enhance() is blocking — callers must NOT call from the main thread.
 */
object LlmEnhancer {

    private const val PREFS_NAME  = "llm_crash_guard"
    private const val KEY_LOADING = "hw_init_model"

    private var engine: Engine? = null
    private var loadedModelId: String? = null
    var activeBackend: String = "none"
        private set

    private var prefs: SharedPreferences? = null

    fun bindContext(ctx: Context) {
        prefs = ctx.applicationContext.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
    }

    fun isReady(): Boolean = engine != null

    fun init(modelFile: File, modelId: String, nativeLibraryDir: String = "",
             npuOnly: Boolean = false): Boolean {
        if (loadedModelId == modelId && isReady()) return true
        destroy()

        val p = prefs
        val prevCrashModel = p?.getString(KEY_LOADING, null)
        val skipHardware   = (prevCrashModel != null)

        if (skipHardware) {
            if (npuOnly) {
                // NPU-only model: CPU/GPU have no standard LiteRT tensors to run.
                // Leave the sentinel so we don't retry NPU on every startup. The user
                // must switch to a CPU model or wait for a library/model update.
                DebugLog.w("LlmEnhancer",
                    "crash sentinel present for NPU-only model $modelId — NPU unavailable, " +
                    "CPU not supported by this model. Sentinel preserved. Switch to a CPU model.")
                return false
            }
            DebugLog.w("LlmEnhancer",
                "crash sentinel present (model=$prevCrashModel) — skipping NPU/GPU, going straight to CPU")
        }

        // Write sentinel synchronously (commit, not apply) before any hardware init.
        // If the process dies natively mid-init, the sentinel is guaranteed on disk.
        if (!skipHardware) p?.edit()?.putString(KEY_LOADING, modelId)?.commit()

        val hardwareCandidates = listOf(
            "npu" to { Backend.NPU(nativeLibraryDir) },
            "gpu" to { Backend.GPU() },
        )
        // NPU-only models have no standard tensor graphs — CPU/GPU will always fail.
        val cpuCandidate = "cpu" to { Backend.CPU() }

        val candidates = when {
            skipHardware -> listOf(cpuCandidate)
            npuOnly      -> hardwareCandidates  // no CPU fallback
            else         -> hardwareCandidates + cpuCandidate
        }

        for ((label, makeBackend) in candidates) {
            // Clear sentinel just before CPU so a CPU-level crash doesn't leave a stale key.
            if (label == "cpu") p?.edit()?.remove(KEY_LOADING)?.apply()

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
                engine        = result.getOrThrow()
                loadedModelId = modelId
                activeBackend = label
                p?.edit()?.remove(KEY_LOADING)?.apply()
                DebugLog.i("LlmEnhancer", "loaded $modelId via $label backend nativeLibDir=$nativeLibraryDir")
                return true
            } else {
                DebugLog.e("LlmEnhancer", "$label backend failed for $modelId", result.exceptionOrNull())
            }
        }

        if (!npuOnly) p?.edit()?.remove(KEY_LOADING)?.apply()
        DebugLog.e("LlmEnhancer", "all backends failed for $modelId (npuOnly=$npuOnly)")
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

    /** Clear the crash sentinel so NPU will be retried on next init(). Call only on explicit user action. */
    fun clearCrashSentinel() {
        prefs?.edit()?.remove(KEY_LOADING)?.apply()
        DebugLog.i("LlmEnhancer", "crash sentinel cleared — NPU will be retried on next load")
    }
}
