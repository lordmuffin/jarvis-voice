package com.lordmuffin.jarvisvoice.live

import kotlin.math.min

/** Retry schedule for re-opening a dead microphone: 0.5 s, 1 s, 2 s, 4 s, then every 5 s, for a minute. */
data class RecoveryPolicy(
    val initialDelayMs: Long = 500,
    val maxDelayMs: Long = 5_000,
    val giveUpAfterMs: Long = 60_000,
) {
    /** @return how long to wait before the next try, or null once it has been failing for [giveUpAfterMs]. */
    fun delayMs(attempt: Int, failingForMs: Long): Long? {
        if (failingForMs >= giveUpAfterMs) return null
        val exponent = attempt.coerceIn(0, 20)
        return min(maxDelayMs, initialDelayMs shl exponent)
    }
}
