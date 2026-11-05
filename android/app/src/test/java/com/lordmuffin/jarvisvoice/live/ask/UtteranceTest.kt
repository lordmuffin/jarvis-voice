package com.lordmuffin.jarvisvoice.live.ask

import org.junit.Assert.assertEquals
import org.junit.Test

/** What TtsAskSpeaker reports for one reply: EchoGuard's window rests on these. */
class UtteranceTest {

    private val events = ArrayList<String>()
    private val u = Utterance("ask-1", { events += "first" }, { events += "done" }, { events += "error" })

    @Test fun firstAudioOnceThenOneEnd() {
        u.audio(); u.audio()
        u.finish(ok = true); u.finish(ok = false)
        assertEquals(listOf("first", "done"), events)
    }

    @Test fun losingFocusAfterAudioIsTheEndOfWhatPlayed() {
        u.audio()
        u.lostFocus()
        assertEquals(listOf("first", "done"), events)
    }

    @Test fun losingFocusBeforeAudioIsAFailure() {
        u.lostFocus()
        u.audio()
        assertEquals(listOf("error"), events)
    }

    @Test fun theCallersStopSilencesEverythingAfter() {
        u.audio()
        u.cancel()
        u.finish(ok = true)
        u.lostFocus()
        assertEquals(listOf("first"), events)
    }
}
