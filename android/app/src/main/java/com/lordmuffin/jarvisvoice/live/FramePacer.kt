package com.lordmuffin.jarvisvoice.live

/** Paces frames of silence at real time while the microphone is released (muted). */
class FramePacer(private val frameMs: Long = 100) {
    private var next = -1L

    /** @return how long to sleep before emitting the frame that ends at the next deadline. */
    fun sleepMs(now: Long): Long {
        if (next < 0 || now - next > STALL_RESET_MS) next = now
        next += frameMs
        return maxOf(0L, next - now)
    }

    fun reset() {
        next = -1
    }

    private companion object {
        /** Further behind than this (device slept): restart the schedule instead of bursting frames. */
        const val STALL_RESET_MS = 1_000L
    }
}
