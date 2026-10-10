package com.lordmuffin.jarvisvoice.live

import kotlin.math.max
import kotlin.math.min
import kotlin.math.pow
import kotlin.random.Random

/** Exponential backoff with additive jitter: 0.5 s, 1 s, 2 s … capped at 30 s (same as the Mac). */
data class ReconnectPolicy(
    val initialDelayMs: Long = 500,
    val maxDelayMs: Long = 30_000,
    /** Up to this fraction of the base delay is added on top, scaled by a random value in 0..1. */
    val jitterFraction: Double = 0.25,
) {
    /** @param attempt zero-based retry count. @param random a value in 0..1 (injectable for tests). */
    fun delayMs(attempt: Int, random: Double = Random.nextDouble()): Long {
        val exponent = min(max(attempt, 0), 30).toDouble()
        val base = min(maxDelayMs.toDouble(), initialDelayMs * 2.0.pow(exponent))
        return min(maxDelayMs.toDouble(), base * (1 + jitterFraction * random)).toLong()
    }
}
