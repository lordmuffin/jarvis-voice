package com.lordmuffin.jarvisvoice.speech

data class SttModelConfig(
    val id: String,
    val displayName: String,
    val description: String,
    val fileSizeMb: Int,
    val isDefault: Boolean = false,
    val subdir: String,
    val tarUrl: String,
    val tarSubdir: String,
    val encoderFile: String,
    val decoderFile: String,
    val tokensFile: String,
)

object SttModelRegistry {
    const val PREF_ACTIVE_MODEL = "active_stt_model"
    const val DEFAULT_MODEL_ID  = "base"

    val MODELS = listOf(
        SttModelConfig(
            id          = "tiny",
            displayName = "Whisper Tiny",
            description = "Fastest inference, lowest accuracy.",
            fileSizeMb  = 75,
            subdir      = "models/whisper-tiny-en",
            tarUrl      = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-whisper-tiny.en.tar.bz2",
            tarSubdir   = "sherpa-onnx-whisper-tiny.en",
            encoderFile = "tiny.en-encoder.int8.onnx",
            decoderFile = "tiny.en-decoder.int8.onnx",
            tokensFile  = "tiny.en-tokens.txt",
        ),
        SttModelConfig(
            id          = "base",
            displayName = "Whisper Base",
            description = "Balanced speed and accuracy.",
            fileSizeMb  = 145,
            isDefault   = true,
            subdir      = "models/whisper-base-en",
            tarUrl      = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-whisper-base.en.tar.bz2",
            tarSubdir   = "sherpa-onnx-whisper-base.en",
            encoderFile = "base.en-encoder.int8.onnx",
            decoderFile = "base.en-decoder.int8.onnx",
            tokensFile  = "base.en-tokens.txt",
        ),
        SttModelConfig(
            id          = "small",
            displayName = "Whisper Small",
            description = "Better accuracy, uses more RAM.",
            fileSizeMb  = 490,
            subdir      = "models/whisper-small-en",
            tarUrl      = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-whisper-small.en.tar.bz2",
            tarSubdir   = "sherpa-onnx-whisper-small.en",
            encoderFile = "small.en-encoder.int8.onnx",
            decoderFile = "small.en-decoder.int8.onnx",
            tokensFile  = "small.en-tokens.txt",
        ),
        SttModelConfig(
            id          = "medium",
            displayName = "Whisper Medium",
            description = "Best accuracy. ~1.5 GB download, needs 4+ GB RAM.",
            fileSizeMb  = 1500,
            subdir      = "models/whisper-medium-en",
            tarUrl      = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-whisper-medium.en.tar.bz2",
            tarSubdir   = "sherpa-onnx-whisper-medium.en",
            encoderFile = "medium.en-encoder.int8.onnx",
            decoderFile = "medium.en-decoder.int8.onnx",
            tokensFile  = "medium.en-tokens.txt",
        ),
    )
}
