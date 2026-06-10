package com.lordmuffin.jarvisvoice

data class DictationSession(
    val id: Long,
    val timestamp: Long,
    val transcript: String,       // final output (LLM-enhanced if available)
    val rawTranscript: String,    // Whisper+dict output before LLM pass
    val wordCount: Int,
    val durationMs: Long,
    val wpm: Float
)
