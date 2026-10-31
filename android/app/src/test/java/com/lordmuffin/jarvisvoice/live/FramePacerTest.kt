package com.lordmuffin.jarvisvoice.live

import org.junit.Assert.assertEquals
import org.junit.Test

/** While muted the mic is released, so frames of silence are paced by the clock instead. */
class FramePacerTest {
    @Test fun holdsOneFrameIntervalBetweenFrames() {
        val p = FramePacer(frameMs = 100)
        assertEquals(100L, p.sleepMs(now = 1_000))
        assertEquals(100L, p.sleepMs(now = 1_100))
    }

    @Test fun sleepsOnlyTheRemainderWhenWorkTookTime() {
        val p = FramePacer(frameMs = 100)
        p.sleepMs(now = 0)
        assertEquals(70L, p.sleepMs(now = 130))   // next deadline 200
    }

    @Test fun catchesUpAfterAShortStallWithoutSleeping() {
        val p = FramePacer(frameMs = 100)
        p.sleepMs(now = 0)
        assertEquals(0L, p.sleepMs(now = 250))    // deadline 200 already passed
    }

    @Test fun aLongStallResetsInsteadOfBursting() {
        val p = FramePacer(frameMs = 100)
        p.sleepMs(now = 0)
        p.sleepMs(now = 100)
        assertEquals(100L, p.sleepMs(now = 60_000))
    }

    @Test fun resetStartsFresh() {
        val p = FramePacer(frameMs = 100)
        p.sleepMs(now = 0)
        p.reset()
        assertEquals(100L, p.sleepMs(now = 5_000))
    }
}
