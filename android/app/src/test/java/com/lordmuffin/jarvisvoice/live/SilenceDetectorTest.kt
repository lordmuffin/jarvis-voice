package com.lordmuffin.jarvisvoice.live

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** A phone call takes the mic and Android feeds the app exact zeros: no error, so we have to notice. */
class SilenceDetectorTest {
    private val zeros = ShortArray(1600)
    private val voice = ShortArray(1600) { (it % 50).toShort() }

    @Test fun shortSilenceIsNotAnInterruption() {
        val d = SilenceDetector(thresholdFrames = 15)
        repeat(14) { assertFalse(d.onFrame(zeros)) }
        assertFalse(d.silent)
    }

    @Test fun sustainedZerosFlipOnceAfterTheThreshold() {
        val d = SilenceDetector(thresholdFrames = 15)
        repeat(14) { d.onFrame(zeros) }
        assertTrue(d.onFrame(zeros))      // 15th frame: the change
        assertTrue(d.silent)
        assertFalse(d.onFrame(zeros))     // no repeat notification
    }

    @Test fun firstRealAudioClearsItImmediately() {
        val d = SilenceDetector(thresholdFrames = 3)
        repeat(3) { d.onFrame(zeros) }
        assertTrue(d.onFrame(voice))
        assertFalse(d.silent)
    }

    @Test fun quietButNonZeroAudioIsNotSilence() {
        val d = SilenceDetector(thresholdFrames = 3)
        val hiss = ShortArray(1600).also { it[800] = 1 }
        repeat(10) { d.onFrame(hiss) }
        assertFalse(d.silent)
    }

    @Test fun resetClearsState() {
        val d = SilenceDetector(thresholdFrames = 2)
        repeat(2) { d.onFrame(zeros) }
        d.reset()
        assertFalse(d.silent)
        assertEquals(false, d.onFrame(zeros))
    }
}
