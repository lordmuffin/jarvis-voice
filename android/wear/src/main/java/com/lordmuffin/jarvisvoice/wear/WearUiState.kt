package com.lordmuffin.jarvisvoice.wear

import com.lordmuffin.jarvisvoice.wear.db.SyncStatus

/** Maps the latest recording's sync status to the Instrument Wear states (Ready, Saved, Synced, Failed). */
data class WearUiState(
    val glyph: Glyph,
    val tone: Tone,
    val labelRes: Int,
    val metaRes: Int?,
    val waiting: Int = 0,
    val response: String? = null,
) {
    enum class Glyph { NONE, SQUARE, CHECK, FLAG }
    enum class Tone { NONE, NEUTRAL, SUCCESS, WARNING }

    companion object {
        fun forLatest(status: SyncStatus?, waiting: Int, response: String? = null): WearUiState =
            when (status) {
                null -> WearUiState(Glyph.NONE, Tone.NONE, R.string.wear_state_ready, null)
                SyncStatus.PENDING ->
                    WearUiState(Glyph.SQUARE, Tone.NEUTRAL, R.string.wear_state_saved, null, waiting)
                SyncStatus.SYNCED -> WearUiState(
                    Glyph.CHECK, Tone.SUCCESS, R.string.wear_state_captured, R.string.wear_meta_synced,
                    response = response?.takeIf { it.isNotBlank() },
                )
                SyncStatus.FAILED ->
                    WearUiState(Glyph.FLAG, Tone.WARNING, R.string.wear_state_failed, R.string.wear_meta_failed, waiting)
            }
    }
}
