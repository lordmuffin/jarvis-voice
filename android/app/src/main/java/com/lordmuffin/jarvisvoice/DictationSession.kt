package com.lordmuffin.jarvisvoice

data class DictationSession(
    val id: Long,
    val timestamp: Long,
    val transcript: String,       // final output (LLM-enhanced if available)
    val rawTranscript: String,    // Whisper+dict output before LLM pass
    val wordCount: Int,
    val durationMs: Long,         // recording + STT time combined
    val wpm: Float,
    val llmModel: String = "",    // display name of the LLM model used, empty if base run
    val llmMs: Long = 0L,         // LLM enhancement wall-clock time in ms, 0 if no enhancement
)
