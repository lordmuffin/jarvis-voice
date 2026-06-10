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

    // LiteRT-LM .litertlm models from litert-community on HuggingFace.
    // Downloads require a HuggingFace account with Gemma license accepted
    // (https://huggingface.co/litert-community) and a HF token entered in Settings.
    val MODELS = listOf(
        ModelConfig(
            id = "gemma4-12b",
            displayName = "Gemma 4 12B",
            description = "Best quality · 6.5 GB download · 8+ GB RAM",
            fileSizeMb = 6700,
            minRamGb = 8,
            downloadUrl = "https://huggingface.co/litert-community/gemma-4-12B-it-litert-lm/resolve/main/gemma-4-12B-it.litertlm",
            filename = "gemma4-12b.litertlm",
            isDefault = true
        ),
        ModelConfig(
            id = "gemma4-2b",
            displayName = "Gemma 4 2B",
            description = "Efficient 2B · 2.6 GB download · 4 GB RAM",
            fileSizeMb = 2590,
            minRamGb = 4,
            downloadUrl = "https://huggingface.co/litert-community/gemma-4-E2B-it-litert-lm/resolve/main/gemma-4-E2B-it.litertlm",
            filename = "gemma4-2b.litertlm"
        ),
        ModelConfig(
            id = "gemma3-1b",
            displayName = "Gemma 3 1B",
            description = "Fast · 530 MB download · 4 GB RAM+",
            fileSizeMb = 530,
            minRamGb = 4,
            downloadUrl = "https://huggingface.co/litert-community/Gemma3-1B-IT/resolve/main/gemma3-1b-it-int4.litertlm",
            filename = "gemma3-1b.litertlm"
        )
    )

    const val NO_LLM = "none"
    val DEFAULT = MODELS.first { it.isDefault }
}
