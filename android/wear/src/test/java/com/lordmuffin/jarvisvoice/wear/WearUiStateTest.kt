package com.lordmuffin.jarvisvoice.wear

import com.lordmuffin.jarvisvoice.wear.db.SyncStatus
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class WearUiStateTest {

    @Test fun `no recording is ready`() {
        val s = WearUiState.forLatest(null, waiting = 0)
        assertEquals(WearUiState.Tone.NONE, s.tone)
        assertEquals(R.string.wear_state_ready, s.labelRes)
        assertNull(s.response)
    }

    @Test fun `pending shows saved with waiting count`() {
        val s = WearUiState.forLatest(SyncStatus.PENDING, waiting = 2)
        assertEquals(R.string.wear_state_saved, s.labelRes)
        assertEquals(WearUiState.Glyph.SQUARE, s.glyph)
        assertEquals(2, s.waiting)
    }

    @Test fun `synced is success and server response wins`() {
        val s = WearUiState.forLatest(SyncStatus.SYNCED, waiting = 0, response = "Noted.")
        assertEquals(WearUiState.Tone.SUCCESS, s.tone)
        assertEquals(R.string.wear_state_captured, s.labelRes)
        assertEquals("Noted.", s.response)
        assertNull(WearUiState.forLatest(SyncStatus.SYNCED, 0, "  ").response)
    }

    @Test fun `failed is warning`() {
        val s = WearUiState.forLatest(SyncStatus.FAILED, waiting = 1)
        assertEquals(WearUiState.Tone.WARNING, s.tone)
        assertEquals(R.string.wear_state_failed, s.labelRes)
        assertEquals(R.string.wear_meta_failed, s.metaRes)
    }
}
