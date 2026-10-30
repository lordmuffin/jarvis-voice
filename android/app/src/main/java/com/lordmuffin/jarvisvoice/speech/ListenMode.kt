package com.lordmuffin.jarvisvoice.speech

/** How a listening session ends. */
enum class ListenMode(
    /** The first transcribed phrase ends the session, so a conversation turn goes out at the speaker's pause. */
    val endsAtPause: Boolean,
    /** This much silence ends the session on its own; null = only the caller ends it. */
    val silenceStopMs: Long?,
) {
    /** Voice chat: each phrase is a turn. */
    CONVERSATION(endsAtPause = true, silenceStopMs = null),
    /** Tap to dictate: listens through pauses until tapped again, or until a minute of silence. */
    DICTATION(endsAtPause = false, silenceStopMs = 60_000L),
    /** Press and hold: listens until released. */
    HOLD(endsAtPause = false, silenceStopMs = null),
}
