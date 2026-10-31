package com.lordmuffin.jarvisvoice.pill

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The dictation pill's state machine: Idle, Listening, Processing, Done and the Error states. */
class PillModelTest {

    private val done = PillState.Done(words = 8, wpm = 142, enhanced = true, copied = false)

    @Test fun startsIdle() {
        assertEquals(PillState.Idle, PillModel().state.value)
    }

    @Test fun tapWhileIdleStartsListening() {
        val pill = PillModel()
        assertTrue(pill.startListening(nowMs = 1_000, held = false))
        assertEquals(PillState.Listening(startedAtMs = 1_000, held = false), pill.state.value)
    }

    @Test fun holdRemembersItIsHeld() {
        val pill = PillModel()
        pill.startListening(nowMs = 5, held = true)
        assertEquals(PillState.Listening(5, held = true), pill.state.value)
    }

    @Test fun stoppingListeningProcesses() {
        val pill = PillModel()
        pill.startListening(0, false)
        assertTrue(pill.stopListening())
        assertEquals(PillState.Processing, pill.state.value)
    }

    @Test fun tapsDuringProcessingAreIgnored() {
        val pill = PillModel()
        pill.startListening(0, false)
        pill.stopListening()
        assertFalse(pill.startListening(10, false))
        assertFalse(pill.stopListening())
        assertEquals(PillState.Processing, pill.state.value)
    }

    @Test fun stopWhileIdleIsIgnored() {
        val pill = PillModel()
        assertFalse(pill.stopListening())
        assertEquals(PillState.Idle, pill.state.value)
    }

    @Test fun finishingAfterProcessingIsDone() {
        val pill = PillModel()
        pill.startListening(0, false)
        pill.stopListening()
        assertTrue(pill.finish(done))
        assertEquals(done, pill.state.value)
    }

    /** Tap-mode dictation ends itself after a pause, so the result lands without a second tap. */
    @Test fun finishingWhileStillListeningIsDone() {
        val pill = PillModel()
        pill.startListening(0, false)
        assertTrue(pill.finish(done))
        assertEquals(done, pill.state.value)
    }

    @Test fun finishFromIdleIsIgnored() {
        val pill = PillModel()
        assertFalse(pill.finish(done))
        assertEquals(PillState.Idle, pill.state.value)
    }

    @Test fun resetReturnsAnyStateToIdle() {
        val pill = PillModel()
        pill.startListening(0, false)
        pill.reset()
        assertEquals(PillState.Idle, pill.state.value)
        pill.fail(PillError.NO_SPEECH)
        pill.reset()
        assertEquals(PillState.Idle, pill.state.value)
    }

    @Test fun failureWhileListeningOrProcessingShowsTheError() {
        for (stop in listOf(false, true)) {
            val pill = PillModel()
            pill.startListening(0, false)
            if (stop) pill.stopListening()
            assertTrue(pill.fail(PillError.MIC_OFF))
            assertEquals(PillState.Error(PillError.MIC_OFF), pill.state.value)
        }
    }

    /** The mic can be refused before a recording starts (permission revoked, a Live session owns it). */
    @Test fun failureFromIdleShowsTheError() {
        val pill = PillModel()
        assertTrue(pill.fail(PillError.MIC_BUSY))
        assertEquals(PillState.Error(PillError.MIC_BUSY), pill.state.value)
    }

    @Test fun tapOnAnErrorRetries() {
        val pill = PillModel()
        pill.fail(PillError.NO_SPEECH)
        assertTrue(pill.startListening(2_000, false))
        assertEquals(PillState.Listening(2_000, false), pill.state.value)
    }

    @Test fun lateResultsAfterFailureAreIgnored() {
        val pill = PillModel()
        pill.startListening(0, false)
        pill.fail(PillError.ENGINE_UNAVAILABLE)
        assertFalse(pill.finish(done))
        assertEquals(PillState.Error(PillError.ENGINE_UNAVAILABLE), pill.state.value)
    }

    @Test fun doneAndErrorsClearThemselvesIdleAndListeningDoNot() {
        assertEquals(1_500L, done.autoResetMs)
        assertEquals(6_000L, PillState.Error(PillError.NO_SPEECH).autoResetMs)
        assertNull(PillState.Idle.autoResetMs)
        assertNull(PillState.Listening(0, false).autoResetMs)
        assertNull(PillState.Processing.autoResetMs)
    }

    @Test fun onlyListeningAndProcessingBlockTheOverlayFromHiding() {
        assertTrue(PillState.Listening(0, false).busy)
        assertTrue(PillState.Processing.busy)
        assertFalse(PillState.Idle.busy)
        assertFalse(done.busy)
        assertFalse(PillState.Error(PillError.MIC_OFF).busy)
    }

    @Test fun errorsThatNeedSettingsOpenThemOnTap() {
        assertEquals(PillAction.OPEN_SETTINGS, PillError.MIC_OFF.tapAction)
        assertEquals(PillAction.OPEN_STT_SETTINGS, PillError.ENGINE_UNAVAILABLE.tapAction)
        assertEquals(PillAction.RETRY, PillError.NO_SPEECH.tapAction)
        assertEquals(PillAction.NONE, PillError.MIC_BUSY.tapAction)
    }
}
