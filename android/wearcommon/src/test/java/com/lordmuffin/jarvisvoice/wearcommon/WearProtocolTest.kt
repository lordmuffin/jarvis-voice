package com.lordmuffin.jarvisvoice.wearcommon

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class WearProtocolTest {
    @Test fun `meta survives the channel path`() {
        val m = RecordingMeta("w-1700000000000-ab12", startedAtMs = 1_700_000_000_000, durationMs = 61_500, bytes = 184_320)
        assertEquals(m, RecordingMeta.fromPath(m.toPath()))
    }

    @Test fun `paths that are not recordings are refused`() {
        assertNull(RecordingMeta.fromPath("/jarvis/other?id=a&t=1&d=1&b=1"))
        assertNull(RecordingMeta.fromPath("/jarvis/recording"))
        assertNull(RecordingMeta.fromPath("/jarvis/recording?id=a&t=1&d=1"))        // no byte count
        assertNull(RecordingMeta.fromPath("/jarvis/recording?id=a&t=x&d=1&b=5"))    // not a number
        assertNull(RecordingMeta.fromPath("/jarvis/recording?id=a&t=1&d=1&b=0"))    // empty
    }

    @Test fun `an id that could escape the inbox directory is refused`() {
        assertNull(RecordingMeta.fromPath("/jarvis/recording?id=..%2F..%2Fetc%2Fpasswd&t=1&d=1&b=5"))
        assertNull(RecordingMeta.fromPath("/jarvis/recording?id=a%2Fb&t=1&d=1&b=5"))
        assertNull(RecordingMeta.fromPath("/jarvis/recording?id=&t=1&d=1&b=5"))
    }

    @Test fun `ack paths round trip and reject other paths`() {
        assertEquals("w-9", WearPaths.ackIdFromPath(WearPaths.ackPath("w-9")))
        assertNull(WearPaths.ackIdFromPath("/jarvis/config"))
        assertNull(WearPaths.ackIdFromPath("/jarvis/ack/"))
    }

    @Test fun `ack status wire names`() {
        assertEquals(AckStatus.ACKED, AckStatus.fromWire("acked"))
        assertEquals(AckStatus.REJECTED, AckStatus.fromWire("rejected"))
        assertNull(AckStatus.fromWire("???"))
    }
}
