package com.lordmuffin.jarvisvoice.wear

import org.junit.Assert.assertEquals
import org.junit.Test

class RecorderStateTest {
    @Test fun `elapsed time reads m_ss under an hour and h_mm_ss after`() {
        assertEquals("0:00", RecorderState.format(0))
        assertEquals("0:09", RecorderState.format(9_999))
        assertEquals("1:05", RecorderState.format(65_000))
        assertEquals("59:59", RecorderState.format(3_599_000))
        assertEquals("1:00:00", RecorderState.format(3_600_000))
    }

    @Test fun `elapsed never goes negative`() {
        val a = RecorderState.Active("w", startedAtWallMs = 0, startedAtElapsedMs = 500)
        assertEquals(0, RecorderState.elapsedMs(100, a))
        assertEquals(500, RecorderState.elapsedMs(1000, a))
    }
}
