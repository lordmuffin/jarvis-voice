package com.lordmuffin.jarvisvoice.wear

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

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

    @Test fun `only the running recording's file counts as active`() {
        RecorderState.started(RecorderState.Active("w-1", 0, 0))
        assertTrue(RecorderState.isActive(File("/rec/w-1.aac")))
        assertFalse(RecorderState.isActive(File("/rec/w-2.aac")))
        RecorderState.stopped()
        assertFalse(RecorderState.isActive(File("/rec/w-1.aac")))
    }

    @Test fun `a failure stays until the wearer has seen it`() {
        RecorderState.failed(RecorderState.Failure.STOPPED_EARLY)
        assertEquals(RecorderState.Failure.STOPPED_EARLY, RecorderState.failure.value)
        RecorderState.clearFailure()
        assertNull(RecorderState.failure.value)
    }
}
