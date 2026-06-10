package com.lordmuffin.jarvisvoice

data class ModelConfig(
    val id: String,
    val displayName: String,
    val description: String,
    val fileSizeMb: Int,
    val minRamGb: Int,
    val downloadUrl: String,
    val filename: String,
    val isDefault: Boolean = false
)

object ModelRegistry {

    // Update these URLs when Gemma 4 MediaPipe bundles are published to the model hub.
    // Check: https://developers.google.com/mediapipe/solutions/genai/llm_inference
    val MODELS = listOf(
        ModelConfig(
            id = "gemma4-2b",
            displayName = "Gemma 4 2B",
            description = "Fast · works on most phones · 4 GB RAM+",
            fileSizeMb = 1500,
            minRamGb = 4,
            downloadUrl = "https://storage.googleapis.com/mediapipe-models/llm_inference/gemma-4-2b-it-gpu-int4/1/gemma-4-2b-it-gpu-int4.task",
            filename = "gemma4-2b.task",
            isDefault = true
        ),
        ModelConfig(
            id = "gemma4-12b",
            displayName = "Gemma 4 12B",
            description = "Higher quality · requires 12 GB RAM",
            fileSizeMb = 7000,
            minRamGb = 12,
            downloadUrl = "https://storage.googleapis.com/mediapipe-models/llm_inference/gemma-4-12b-it-gpu-int4/1/gemma-4-12b-it-gpu-int4.task",
            filename = "gemma4-12b.task"
        )
    )

    const val NO_LLM = "none"
    val DEFAULT = MODELS.first { it.isDefault }
}
