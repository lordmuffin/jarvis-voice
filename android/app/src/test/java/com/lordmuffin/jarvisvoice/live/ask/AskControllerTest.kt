package com.lordmuffin.jarvisvoice.live.ask

import android.media.AudioDeviceInfo
import com.lordmuffin.jarvisvoice.live.protocol.ClientMessage
import com.lordmuffin.jarvisvoice.live.protocol.ServerEvent
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** Hold to ask on the phone (docs/plans/voice-push-to-talk.md): what is sent, said and shown. */
class AskControllerTest {

    private class FakeSpeaker : AskSpeaker {
        val said = ArrayList<String>()
        var stopped = 0
        var firstAudio: (() -> Unit)? = null
        var done: (() -> Unit)? = null
        var failed: (() -> Unit)? = null

        override fun speak(text: String, onFirstAudio: () -> Unit, onDone: () -> Unit, onError: () -> Unit) {
            said += text
            firstAudio = onFirstAudio
            done = onDone
            failed = onError
        }

        override fun stop() { stopped++ }
    }

    private var now = 10_000L
    private var closed = true
    private val sent = ArrayList<ClientMessage>()
    private val speaker = FakeSpeaker()
    private val ask = AskController(
        send = { sent += it },
        speaker = speaker,
        routeClosed = { closed },
        clock = { now },
        streamMs = { 99 },
        newId = { "a1" },
    )

    private fun reply(text: String = "Priya runs it.", outcome: String = "answer", id: String = "a1") =
        ask.onReply(ServerEvent.AskReply(id, outcome, text))

    private fun askAndRelease() {
        ask.onAvailable(true)
        ask.down(tMs = 60_000)
        now += 2_000
        ask.up(tMs = 62_000)
    }

    @Test fun hiddenUntilTheServerOffersIt() {
        assertEquals(AskUi.Hidden, ask.ui.value)
        ask.down(tMs = 1)
        assertTrue(sent.isEmpty())
        ask.onAvailable(true)
        assertEquals(AskUi.Ready, ask.ui.value)
    }

    @Test fun aHoldSendsItsMarkersOnTheFramesClock() {
        askAndRelease()
        assertEquals(listOf(ClientMessage.AskBegin("a1", 60_000), ClientMessage.AskEnd("a1", 62_000)), sent)
        assertEquals(AskUi.Waiting, ask.ui.value)
    }

    @Test fun theReplyIsSpokenAndTimedFromRelease() {
        askAndRelease()
        now += 1_000
        reply()
        assertEquals(listOf("Priya runs it."), speaker.said)
        now += 2_400
        speaker.firstAudio!!()
        assertEquals(
            ClientMessage.AskPlayed("a1", "started", tMs = 99, latencyMs = 3_400),
            sent.last(),
        )
        assertEquals(AskUi.Speaking("Priya runs it."), ask.ui.value)
        speaker.done!!()
        assertEquals("done", (sent.last() as ClientMessage.AskPlayed).state)
        assertEquals(AskUi.Answered("Priya runs it.", spoken = true), ask.ui.value)
    }

    @Test fun noHeadphonesMeansTextOnly() {
        askAndRelease()
        closed = false
        reply()
        assertTrue(speaker.said.isEmpty())
        val played = sent.last() as ClientMessage.AskPlayed
        assertEquals("skipped" to "route", played.state to played.reason)
        assertEquals(AskUi.Answered("Priya runs it.", spoken = false, why = AskUi.Why.ROUTE), ask.ui.value)
    }

    @Test fun headphonesLostWhileSpeakingStopAtOnce() {
        askAndRelease()
        reply()
        speaker.firstAudio!!()
        ask.onRouteChanged(closed = false)
        assertEquals(1, speaker.stopped)
        val played = sent.last() as ClientMessage.AskPlayed
        assertEquals("skipped" to "interrupted", played.state to played.reason)
    }

    @Test fun aLateReplyIsShownNotSpoken() {
        askAndRelease()
        now += 31_000
        reply()
        assertTrue(speaker.said.isEmpty())
        assertEquals("stale", (sent.last() as ClientMessage.AskPlayed).reason)
    }

    @Test fun aReplyToAnOlderAskIsIgnored() {
        askAndRelease()
        reply(id = "old")
        assertTrue(speaker.said.isEmpty())
        assertEquals(AskUi.Waiting, ask.ui.value)
    }

    @Test fun aNewHoldStopsSpeechAndSupersedesTheLast() {
        askAndRelease()
        reply()
        speaker.firstAudio!!()
        ask.down(tMs = 70_000)
        assertEquals(1, speaker.stopped)
        assertEquals(AskUi.Listening, ask.ui.value)
    }

    @Test fun nothingHeardSaysNothing() {
        askAndRelease()
        reply(text = "", outcome = "empty")
        assertTrue(speaker.said.isEmpty())
        assertEquals(AskUi.Answered("", spoken = false, why = AskUi.Why.NOTHING_HEARD), ask.ui.value)
    }

    @Test fun aSpeechFailureIsReported() {
        askAndRelease()
        reply()
        speaker.failed!!()
        assertEquals("tts_failed", (sent.last() as ClientMessage.AskPlayed).reason)
        assertEquals(AskUi.Answered("Priya runs it.", spoken = false, why = AskUi.Why.SPEECH_FAILED), ask.ui.value)
    }

    @Test fun losingTheConnectionEndsEverything() {
        askAndRelease()
        reply()
        ask.onAvailable(false)
        assertEquals(1, speaker.stopped)
        assertEquals(AskUi.Hidden, ask.ui.value)
        val before = sent.size
        ask.down(tMs = 80_000)
        assertEquals(before, sent.size)
    }

    @Test fun onlyClosedOutputsCount() {
        assertTrue(OutputRoutes.isClosed(AudioDeviceInfo.TYPE_WIRED_HEADPHONES))
        assertTrue(OutputRoutes.isClosed(AudioDeviceInfo.TYPE_BLUETOOTH_A2DP))
        assertTrue(OutputRoutes.isClosed(AudioDeviceInfo.TYPE_BLE_HEADSET))
        assertTrue(OutputRoutes.isClosed(AudioDeviceInfo.TYPE_USB_HEADSET))
        assertFalse(OutputRoutes.isClosed(AudioDeviceInfo.TYPE_BUILTIN_SPEAKER))
        assertFalse(OutputRoutes.isClosed(AudioDeviceInfo.TYPE_BUILTIN_EARPIECE))
        assertFalse(OutputRoutes.isClosed(AudioDeviceInfo.TYPE_BLE_SPEAKER))
        assertFalse(OutputRoutes.isClosed(AudioDeviceInfo.TYPE_HDMI))
        assertFalse(OutputRoutes.isClosed(AudioDeviceInfo.TYPE_UNKNOWN))
        assertFalse(OutputRoutes.allClosed(emptyList()))
        assertFalse(OutputRoutes.allClosed(listOf(AudioDeviceInfo.TYPE_BLUETOOTH_A2DP, AudioDeviceInfo.TYPE_BUILTIN_SPEAKER)))
        assertTrue(OutputRoutes.allClosed(listOf(AudioDeviceInfo.TYPE_BLUETOOTH_A2DP)))
    }
}
