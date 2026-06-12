package com.lordmuffin.jarvisvoice

data class ModelConfig(
    val id: String,
    val displayName: String,
    val description: String,
    val fileSizeMb: Int,
    val minRamGb: Int,
    val downloadUrl: String,
    val filename: String,
    val isDefault: Boolean = false,
    // NPU-compiled models have no standard LiteRT tensors — CPU/GPU backends will
    // always fail with "Input tensor not found". Only attempt NPU for these.
    val npuOnly: Boolean = false,
    // System-managed model via Android AI Core (Gemini Nano). No file to download or delete.
    val isAiCore: Boolean = false
)

object ModelRegistry {

    // LiteRT-LM .litertlm models from litert-community on HuggingFace.
    // Downloads require a HuggingFace account with Gemma license accepted
    // (https://huggingface.co/litert-community) and a HF token entered in Settings.
    //
    // NPU models: compiled with hardware-specific subgraphs; must match device SoC.
    // CPU models: universal fallback, run on any device.
    val MODELS = listOf(
        ModelConfig(
            id = "aicore",
            displayName = "Gemini Nano (AI Core)",
            description = "NPU · System model · No download · Pixel 8+ / Android 14+",
            fileSizeMb = 0,
            minRamGb = 0,
            downloadUrl = "",
            filename = "",
            isDefault = true,
            isAiCore = true
        ),
        ModelConfig(
            id = "gemma4-e2b-tensor-g5",
            displayName = "Gemma 4 E2B (Tensor G5)",
            description = "NPU · QAT optimized · 3.95 GB download · 4 GB RAM · Pixel 9+",
            fileSizeMb = 3950,
            minRamGb = 4,
            downloadUrl = "https://huggingface.co/litert-community/gemma-4-E2B-it-litert-lm/resolve/main/gemma-4-E2B-it_Google_Tensor_G5.litertlm",
            filename = "gemma4-e2b-tensor-g5.litertlm",
            isDefault = false,
            npuOnly = true
        ),
        ModelConfig(
            id = "gemma4-12b",
            displayName = "Gemma 4 12B",
            description = "CPU · Best quality · 6.5 GB download · 8+ GB RAM",
            fileSizeMb = 6700,
            minRamGb = 8,
            downloadUrl = "https://huggingface.co/litert-community/gemma-4-12B-it-litert-lm/resolve/main/gemma-4-12B-it.litertlm",
            filename = "gemma4-12b.litertlm"
        ),
        ModelConfig(
            id = "gemma4-e4b",
            displayName = "Gemma 4 E4B",
            description = "CPU · QAT 4B · 3.66 GB download · 6 GB RAM",
            fileSizeMb = 3660,
            minRamGb = 6,
            downloadUrl = "https://huggingface.co/litert-community/gemma-4-E4B-it-litert-lm/resolve/main/gemma-4-E4B-it.litertlm",
            filename = "gemma4-e4b.litertlm"
        ),
        ModelConfig(
            id = "gemma4-2b",
            displayName = "Gemma 4 E2B",
            description = "CPU · QAT 2B · 2.6 GB download · 4 GB RAM",
            fileSizeMb = 2590,
            minRamGb = 4,
            downloadUrl = "https://huggingface.co/litert-community/gemma-4-E2B-it-litert-lm/resolve/main/gemma-4-E2B-it.litertlm",
            filename = "gemma4-2b.litertlm"
        ),
        ModelConfig(
            id = "gemma3-1b",
            displayName = "Gemma 3 1B",
            description = "CPU · Fast · 530 MB download · 4 GB RAM",
            fileSizeMb = 530,
            minRamGb = 4,
            downloadUrl = "https://huggingface.co/litert-community/Gemma3-1B-IT/resolve/main/gemma3-1b-it-int4.litertlm",
            filename = "gemma3-1b.litertlm"
        )
    )

    const val NO_LLM = "none"
    val DEFAULT = MODELS.first { it.isDefault }
}
