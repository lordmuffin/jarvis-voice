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

    // MediaPipe LLM Inference .task models (Gemma 2 series).
    // These URLs require a free Kaggle account and license acceptance:
    //   https://www.kaggle.com/models/google/gemma/frameworks/tfLite
    // If the in-app download returns HTTP 403, download the .task file manually from
    // Kaggle and place it in Android/data/dev.apj.jarvis.voice/files/llm_models/.
    val MODELS = listOf(
        ModelConfig(
            id = "gemma2-2b",
            displayName = "Gemma 2 2B",
            description = "Fast · works on most phones · 4 GB RAM+",
            fileSizeMb = 1400,
            minRamGb = 4,
            downloadUrl = "https://storage.googleapis.com/mediapipe-models/llm_inference/gemma2-2b-it-gpu-int4/float16/1/gemma2-2b-it-gpu-int4.task",
            filename = "gemma2-2b.task",
            isDefault = true
        ),
        ModelConfig(
            id = "gemma2-9b",
            displayName = "Gemma 2 9B",
            description = "Higher quality · requires 12 GB RAM",
            fileSizeMb = 5400,
            minRamGb = 12,
            downloadUrl = "https://storage.googleapis.com/mediapipe-models/llm_inference/gemma2-9b-it-gpu-int4/float16/1/gemma2-9b-it-gpu-int4.task",
            filename = "gemma2-9b.task"
        )
    )

    const val NO_LLM = "none"
    val DEFAULT = MODELS.first { it.isDefault }
}
