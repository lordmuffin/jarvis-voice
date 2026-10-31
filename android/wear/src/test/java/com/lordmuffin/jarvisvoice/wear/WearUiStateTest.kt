package com.lordmuffin.jarvisvoice.wear

import com.lordmuffin.jarvisvoice.wearcommon.sync.QueueItem
import com.lordmuffin.jarvisvoice.wearcommon.sync.SyncState
import org.junit.Assert.assertEquals
import org.junit.Test

class WearUiStateTest {
    private fun item(state: SyncState, id: String = "x") = QueueItem(id, "/f/$id.aac", 1, 1000, 100, state)

    @Test fun `an empty queue is ready`() {
        val s = WearUiState.forQueue(emptyList())
        assertEquals(WearUiState.Tone.NONE, s.tone)
        assertEquals(R.string.wear_state_ready, s.labelRes)
    }

    @Test fun `an unsent recording shows saved with the number waiting`() {
        val s = WearUiState.forQueue(listOf(item(SyncState.RECORDED, "a"), item(SyncState.AT_PHONE, "b")))
        assertEquals(R.string.wear_state_saved, s.labelRes)
        assertEquals(WearUiState.Glyph.SQUARE, s.glyph)
        assertEquals(2, s.waiting)
    }

    @Test fun `everything at the phone is sent, waiting for the server`() {
        val s = WearUiState.forQueue(listOf(item(SyncState.AT_PHONE)))
        assertEquals(R.string.wear_state_sent, s.labelRes)
        assertEquals(WearUiState.Tone.SUCCESS, s.tone)
    }

    @Test fun `a blocked or refused recording outranks progress`() {
        for (stuck in listOf(SyncState.BLOCKED, SyncState.QUARANTINED)) {
            val s = WearUiState.forQueue(listOf(item(SyncState.RECORDED, "a"), item(stuck, "b")))
            assertEquals(WearUiState.Tone.WARNING, s.tone)
            assertEquals(R.string.wear_state_failed, s.labelRes)
            assertEquals(1, s.waiting)
        }
    }

    @Test fun `a failed recording is on screen, whatever the queue is doing`() {
        val cases = mapOf(
            RecorderState.Failure.MIC_UNAVAILABLE to R.string.wear_failed_mic,
            RecorderState.Failure.NOTHING_CAPTURED to R.string.wear_failed_empty,
            RecorderState.Failure.STOPPED_EARLY to R.string.wear_failed_stopped,
        )
        for ((failure, label) in cases) {
            val s = WearUiState.forQueue(listOf(item(SyncState.RECORDED, "a")), failure)
            assertEquals(WearUiState.Tone.WARNING, s.tone)
            assertEquals(WearUiState.Glyph.FLAG, s.glyph)
            assertEquals(label, s.labelRes)
            assertEquals("the queue count stays visible", 1, s.waiting)
        }
    }

    @Test fun `with no failure the queue decides`() {
        assertEquals(R.string.wear_state_ready, WearUiState.forQueue(emptyList(), null).labelRes)
    }
}
