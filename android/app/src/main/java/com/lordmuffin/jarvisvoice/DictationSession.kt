package com.lordmuffin.jarvisvoice

data class DictationSession(
    val id: Long,
    val timestamp: Long,
    val transcript: String,
    val wordCount: Int,
    val durationMs: Long,
    val wpm: Float
)
