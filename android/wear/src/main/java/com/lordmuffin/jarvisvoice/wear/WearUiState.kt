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
        fun forQueue(items: List<QueueItem>, failure: RecorderState.Failure? = null): WearUiState {
            // Anything stuck outranks progress: the wearer needs to see it.
            val stuck = items.count { it.state == SyncState.BLOCKED || it.state == SyncState.QUARANTINED }
            val unsent = items.count { it.state == SyncState.RECORDED }
            val atPhone = items.count { it.state == SyncState.AT_PHONE }
            return when {
                // What just went wrong comes first: the wearer tapped, and needs to know why it failed.
                failure != null -> WearUiState(
                    Glyph.FLAG, Tone.WARNING,
                    when (failure) {
                        RecorderState.Failure.MIC_UNAVAILABLE -> R.string.wear_failed_mic
                        RecorderState.Failure.NOTHING_CAPTURED -> R.string.wear_failed_empty
                        RecorderState.Failure.STOPPED_EARLY -> R.string.wear_failed_stopped
                    },
                    null,
                    stuck + unsent + atPhone,
                )
                stuck > 0 -> WearUiState(Glyph.FLAG, Tone.WARNING, R.string.wear_state_failed, R.string.wear_meta_failed, stuck)
                unsent > 0 -> WearUiState(Glyph.SQUARE, Tone.NEUTRAL, R.string.wear_state_saved, null, unsent + atPhone)
                atPhone > 0 -> WearUiState(Glyph.CHECK, Tone.SUCCESS, R.string.wear_state_sent, R.string.wear_meta_at_phone, atPhone)
                else -> WearUiState(Glyph.NONE, Tone.NONE, R.string.wear_state_ready, null)
            }
        }
    }
}
