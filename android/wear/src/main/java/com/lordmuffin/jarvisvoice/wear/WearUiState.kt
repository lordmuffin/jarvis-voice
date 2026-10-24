package com.lordmuffin.jarvisvoice.wear

import com.lordmuffin.jarvisvoice.wearcommon.sync.QueueItem
import com.lordmuffin.jarvisvoice.wearcommon.sync.SyncState

/** Maps the sync queue to the Instrument Wear states: Ready, Saved, Sent, Needs attention. */
data class WearUiState(
    val glyph: Glyph,
    val tone: Tone,
    val labelRes: Int,
    val metaRes: Int?,
    val waiting: Int = 0,
) {
    enum class Glyph { NONE, SQUARE, CHECK, FLAG }
    enum class Tone { NONE, NEUTRAL, SUCCESS, WARNING }

    companion object {
        fun forQueue(items: List<QueueItem>): WearUiState {
            // Anything stuck outranks progress: the wearer needs to see it.
            val stuck = items.count { it.state == SyncState.BLOCKED || it.state == SyncState.QUARANTINED }
            val unsent = items.count { it.state == SyncState.RECORDED }
            val atPhone = items.count { it.state == SyncState.AT_PHONE }
            return when {
                stuck > 0 -> WearUiState(Glyph.FLAG, Tone.WARNING, R.string.wear_state_failed, R.string.wear_meta_failed, stuck)
                unsent > 0 -> WearUiState(Glyph.SQUARE, Tone.NEUTRAL, R.string.wear_state_saved, null, unsent + atPhone)
                atPhone > 0 -> WearUiState(Glyph.CHECK, Tone.SUCCESS, R.string.wear_state_sent, R.string.wear_meta_at_phone, atPhone)
                else -> WearUiState(Glyph.NONE, Tone.NONE, R.string.wear_state_ready, null)
            }
        }
    }
}
