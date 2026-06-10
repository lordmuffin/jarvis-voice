package com.lordmuffin.jarvisvoice

object TranscriptProcessor {

    // Bracket/paren patterns that mark ambient noise artifacts, e.g. "(buzzer)", "[AUDIO]"
    private val BRACKET_PATTERN = Regex("""[\[\(][^\]\)]{1,40}[\]\)]""")

    // Standalone filler words to strip when filler removal is enabled
    private val FILLER_WORDS = listOf(
        "um", "umm", "uh", "uhh", "hmm", "hm", "er", "err", "ah",
        "you know", "i mean", "sort of", "kind of", "basically", "literally",
        "right", "okay so", "so like", "i guess"
    )

    // Noise phrases that indicate ambient transcription, not real speech
    private val AMBIENT_TRIGGERS = listOf(
        "buzzer", "beep", "ding", "chime", "notification",
        "audio", "music", "sound effect", "ring", "ringtone"
    )

    /**
     * Returns true if the text looks like ambient noise rather than intentional speech.
     * Only flags as ambient if the text is entirely brackets/noise markers.
     */
    fun isAmbientNoise(text: String): Boolean {
        if (text.isBlank()) return true
        val lower = text.lowercase().trim()
        // Strip all bracket patterns — if nothing meaningful remains, it's ambient
        val stripped = BRACKET_PATTERN.replace(lower, "").replace(Regex("\\s+"), " ").trim()
        if (stripped.isEmpty()) return true
        // Short result that only contains ambient trigger words
        val wordCount = stripped.split(" ").filter { it.isNotBlank() }.size
        return wordCount <= 2 && AMBIENT_TRIGGERS.any { stripped.contains(it) }
    }

    fun removeBrackets(text: String): String =
        BRACKET_PATTERN.replace(text, "").replace(Regex("\\s{2,}"), " ").trim()

    fun removeFiller(text: String): String {
        var result = text
        // Longer phrases first to avoid partial matches
        for (filler in FILLER_WORDS.sortedByDescending { it.length }) {
            result = result.replace(
                Regex("(?i)\\b${Regex.escape(filler)}\\b,?\\s*"),
                " "
            )
        }
        return result.replace(Regex("\\s{2,}"), " ").trim()
    }

    /**
     * Full Phase-1 pipeline: ambient check → remove brackets → optional filler strip → trim.
     * Returns null if the result is empty or ambient noise (should be discarded).
     */
    fun process(text: String, removeFillersEnabled: Boolean = false): String? {
        if (isAmbientNoise(text)) {
            DebugLog.i("Processor", "ambient discard: \"${text.take(60)}\"")
            return null
        }
        var result = removeBrackets(text)
        if (result.isBlank()) return null
        if (removeFillersEnabled) result = removeFiller(result)
        result = result.replace(Regex("\\s{2,}"), " ").trim()
        return result.ifBlank { null }
    }
}
