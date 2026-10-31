package com.lordmuffin.jarvisvoice.live

import com.lordmuffin.jarvisvoice.live.DrivingTrigger.Action
import org.junit.Assert.assertEquals
import org.junit.Test

/** What the driving shortcut does. It can't ask questions, so every case ends in a clear action. */
class DrivingTriggerTest {

    @Test fun idleStartsASession() {
        assertEquals(Action.START, DrivingTrigger.decide(active = false, micGranted = true, configured = true))
    }

    /** Offline or not signed in: still record. The service falls back to a local-only session. */
    @Test fun unconfiguredStartsLocalOnly() {
        assertEquals(Action.START_LOCAL, DrivingTrigger.decide(active = false, micGranted = true, configured = false))
    }

    /** A second tap must never stop a drive's recording; it marks the moment instead. */
    @Test fun activeSessionGetsAMarkerNotAStop() {
        assertEquals(Action.MARK, DrivingTrigger.decide(active = true, micGranted = true, configured = true))
    }

    /** Permission can't be requested while driving; hand off to the Record screen. */
    @Test fun missingMicOpensRecord() {
        assertEquals(Action.OPEN_RECORD, DrivingTrigger.decide(active = false, micGranted = false, configured = true))
    }

    @Test fun titleIsDatedAndSortable() {
        assertEquals("Drive 2026-10-30 08:05", DrivingTrigger.title(java.time.LocalDateTime.of(2026, 10, 30, 8, 5)))
    }
}
