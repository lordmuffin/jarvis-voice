package com.lordmuffin.jarvisvoice.live

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** When the microphone dies mid-drive we retry quietly for a while instead of ending the session. */
class RecoveryPolicyTest {
    private val p = RecoveryPolicy()

    @Test fun backsOffThenCaps() {
        assertEquals(500L, p.delayMs(attempt = 0, failingForMs = 0))
        assertEquals(1_000L, p.delayMs(1, 500))
        assertEquals(2_000L, p.delayMs(2, 1_500))
        assertEquals(4_000L, p.delayMs(3, 3_500))
        assertEquals(5_000L, p.delayMs(4, 7_500))
        assertEquals(5_000L, p.delayMs(20, 30_000))
    }

    @Test fun givesUpAfterTheWindow() {
        assertEquals(5_000L, p.delayMs(10, 59_999))
        assertNull(p.delayMs(11, 60_000))
    }
}
