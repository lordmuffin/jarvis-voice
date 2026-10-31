package com.lordmuffin.jarvisvoice.wear

/**
 * A start or stop asked for by the tile, the complication or a mapped button. The record screen
 * runs it once, when it is on screen: the microphone is only granted to a service started from a
 * visible activity, and the user should see the recording (and its stop button) begin.
 */
class PendingToggle {
    private var pending = false

    fun request() { pending = true }

    /**
     * [asked]: the intent carries the request. [restored]: the screen is being rebuilt. The system
     * rebuilds it from the original intent, so that request already ran; running it again would stop
     * the recording it started.
     */
    fun fromIntent(asked: Boolean, restored: Boolean) {
        if (asked && !restored) request()
    }

    /** True once per request. */
    fun take(): Boolean = pending.also { pending = false }
}
