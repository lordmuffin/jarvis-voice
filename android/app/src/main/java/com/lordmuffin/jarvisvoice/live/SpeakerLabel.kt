package com.lordmuffin.jarvisvoice.live

import com.lordmuffin.jarvisvoice.live.protocol.Channel
import com.lordmuffin.jarvisvoice.live.protocol.Speaker

/**
 * Me/Them is what the user sees everywhere: the phone's mic is Me, system audio is Them. The wire
 * names (`mic`/`system`, `me`/`them`) are the protocol's and stay as they are.
 */
fun Channel.speaker(): Speaker = if (this == Channel.MIC) Speaker.ME else Speaker.THEM

/** From a wire `speaker` (`me`/`them`) or `channel` (`mic`/`system`) name; anything else reads as Me. */
fun speakerFor(wire: String?): Speaker = when (wire) {
    "them", "system" -> Speaker.THEM
    else -> Speaker.ME
}
