package com.lordmuffin.jarvisvoice.live

import com.lordmuffin.jarvisvoice.live.protocol.Channel
import com.lordmuffin.jarvisvoice.live.protocol.Speaker
import org.junit.Assert.assertEquals
import org.junit.Test

/** Me/Them everywhere: the phone mic is Me, system audio is Them (the wire names stay mic/system, me/them). */
class SpeakerLabelTest {

    @Test fun channelsMapToSpeakers() {
        assertEquals(Speaker.ME, Channel.MIC.speaker())
        assertEquals(Speaker.THEM, Channel.SYSTEM.speaker())
    }

    @Test fun wireNamesMapToSpeakers() {
        assertEquals(Speaker.ME, speakerFor("me"))
        assertEquals(Speaker.THEM, speakerFor("them"))
        assertEquals(Speaker.ME, speakerFor("mic"))
        assertEquals(Speaker.THEM, speakerFor("system"))
    }

    /** `RemoteSegment.speaker` defaults to me, so an unknown value reads as Me rather than hiding the line. */
    @Test fun unknownWireNameIsMe() {
        assertEquals(Speaker.ME, speakerFor(null))
        assertEquals(Speaker.ME, speakerFor("someone"))
    }
}
