package com.lordmuffin.jarvisvoice.wear

/**
 * A start or stop asked for by the tile, the complication or a mapped button. The record screen
 * runs it once, when it is on screen: the microphone is only granted to a service started from a
 * visible activity, and the user should see the recording (and its stop button) begin.
 */
class PendingToggle {
    private var pending = false

    fun request() { pending = true }

    /** True once per request. */
    fun take(): Boolean = pending.also { pending = false }
}
