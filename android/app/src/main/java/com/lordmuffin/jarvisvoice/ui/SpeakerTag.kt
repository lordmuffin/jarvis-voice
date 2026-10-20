package com.lordmuffin.jarvisvoice.ui

import android.widget.TextView
import com.lordmuffin.jarvisvoice.R
import com.lordmuffin.jarvisvoice.live.protocol.Speaker

/** Draws the Me/Them tag: Me is accent-filled, Them violet-filled, each with its on-colour text. */
fun TextView.bindSpeaker(speaker: Speaker) {
    val me = speaker == Speaker.ME
    setText(if (me) R.string.detail_speaker_me else R.string.detail_speaker_them)
    setBackgroundResource(if (me) R.drawable.speaker_tag_me else R.drawable.speaker_tag_them)
    setTextColor(context.getColor(if (me) R.color.inst_on_accent else R.color.inst_on_them))
}
