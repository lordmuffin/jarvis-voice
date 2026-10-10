package com.lordmuffin.jarvisvoice.live

import com.lordmuffin.jarvisvoice.live.protocol.AudioFrame
import com.lordmuffin.jarvisvoice.live.protocol.Channel
import com.lordmuffin.jarvisvoice.live.protocol.ChannelSeqs
import com.lordmuffin.jarvisvoice.live.protocol.ClientMessage
import com.lordmuffin.jarvisvoice.live.protocol.DraftSegment
import com.lordmuffin.jarvisvoice.live.protocol.ProtocolException
import com.lordmuffin.jarvisvoice.live.protocol.RestBodies
import com.lordmuffin.jarvisvoice.live.protocol.ServerEvent
import com.lordmuffin.jarvisvoice.live.protocol.SessionMode
import com.lordmuffin.jarvisvoice.live.protocol.Speaker
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/** Conformance against the shared suite in `protocol/v1/fixtures` (as the server and Swift tests do). */
class ProtocolFixtureTest {

    private val root = File("../../protocol/v1/fixtures").also {
        check(it.isDirectory) { "fixtures not found from ${File(".").absolutePath}" }
    }

    private fun fixture(kind: String, name: String) = File(root, "$kind/$name.json").readText()

    private fun assertSameJson(expected: String, actual: JSONObject) {
        val exp = JSONObject(expected)
        assertTrue("expected $exp\n   got $actual", same(exp, actual))
    }

    /** Structural JSON equality (numbers by value, key order ignored). */
    private fun same(a: Any?, b: Any?): Boolean = when {
        a is JSONObject && b is JSONObject -> {
            val keys = a.keys().asSequence().toSet()
            keys == b.keys().asSequence().toSet() && keys.all { same(a.get(it), b.get(it)) }
        }
        a is JSONArray && b is JSONArray ->
            a.length() == b.length() && (0 until a.length()).all { same(a.get(it), b.get(it)) }
        a is Number && b is Number -> a.toLong() == b.toLong() && a.toDouble() == b.toDouble()
        else -> a == b
    }

    // region Client → server

    @Test fun helloMatchesFixture() {
        assertSameJson(
            fixture("valid", "hello"),
            ClientMessage.Hello("macbook-pro", ChannelSeqs(mic = 41, system = null)).toJson(),
        )
    }

    @Test fun freshHelloMatchesFixture() {
        assertSameJson(fixture("valid", "hello__fresh"), ClientMessage.Hello("macbook-pro", ChannelSeqs()).toJson())
    }

    @Test fun draftSegmentMatchesFixture() {
        assertSameJson(
            fixture("valid", "draft_segment"),
            ClientMessage.Draft(DraftSegment(Channel.MIC, 1000, 2400, "let's get started", final = false)).toJson(),
        )
    }

    @Test fun markerMatchesFixture() {
        assertSameJson(fixture("valid", "marker"), ClientMessage.Marker(5000, "decision").toJson())
    }

    @Test fun endMatchesFixture() {
        assertSameJson(fixture("valid", "end"), ClientMessage.End.toJson())
    }

    @Test fun sessionCreateMatchesFixtures() {
        assertSameJson(
            fixture("valid", "session_create_request__minimal"),
            RestBodies.sessionCreate(SessionMode.SOLO, listOf(Channel.MIC), title = null),
        )
        assertSameJson(
            fixture("valid", "session_create_request"),
            RestBodies.sessionCreate(SessionMode.MEETING, listOf(Channel.MIC, Channel.SYSTEM), "Weekly sync"),
        )
    }

    @Test fun ticketRequestMatchesFixture() {
        assertSameJson(fixture("valid", "ticket_request"), RestBodies.ticket())
    }

    // endregion

    // region Server → client

    private val serverFixtures = listOf(
        "hello_ack", "ack", "segment", "copilot", "copilot__empty", "status", "status__no_tier", "final_note", "error",
    )

    @Test fun parsesEveryValidServerFixture() {
        for (name in serverFixtures) {
            val event = ServerEvent.parse(fixture("valid", name))
            assertTrue("$name parsed to $event", event != null)
        }
    }

    @Test fun parsedValuesMatch() {
        val ack = ServerEvent.parse(fixture("valid", "hello_ack")) as ServerEvent.HelloAck
        assertEquals(ChannelSeqs(mic = 41, system = null), ack.acked)

        val seg = (ServerEvent.parse(fixture("valid", "segment")) as ServerEvent.SegmentEvent).segment
        assertEquals(Channel.SYSTEM, seg.channel)
        assertEquals(Speaker.THEM, seg.speaker)
        assertEquals(1000L, seg.startMs)
        assertEquals("Hello everyone", seg.text)

        val copilot = (ServerEvent.parse(fixture("valid", "copilot")) as ServerEvent.Copilot).snapshot
        assertEquals(3L, copilot.version)
        assertEquals("sam", copilot.actions[0].owner)
        assertNull(copilot.actions[1].owner)
        assertEquals("question", copilot.suggestions[0].kind)

        val status = ServerEvent.parse(fixture("valid", "status__no_tier")) as ServerEvent.Status
        assertNull(status.sttTier)
    }

    @Test fun rejectsInvalidServerFixtures() {
        val invalid = listOf(
            "ack__string_seq", "copilot__bad_suggestion_kind", "error__missing_message",
            "final_note__missing_title", "hello_ack__missing_acked", "segment__bad_speaker", "status__missing_lag",
        )
        for (name in invalid) {
            assertThrows(name, ProtocolException::class.java) { ServerEvent.parse(fixture("invalid", name)) }
        }
    }

    @Test fun ignoresUnknownAndRelayedTypes() {
        assertNull(ServerEvent.parse(fixture("valid", "draft_segment")))
        assertNull(ServerEvent.parse(fixture("valid", "marker")))
        assertNull(ServerEvent.parse("""{"type":"something_new"}"""))
    }

    // endregion

    // region Binary frames

    @Test fun frameRoundTripsHexFixture() {
        val hex = File(root, "frame_mic_seq7.hex").readText().trim()
        val bytes = ByteArray(hex.length / 2) { hex.substring(it * 2, it * 2 + 2).toInt(16).toByte() }
        val frame = AudioFrame.decode(bytes)
        assertEquals(Channel.MIC, frame.channel)
        assertEquals(7L, frame.seq)
        assertEquals(140L, frame.tMs)
        assertEquals(640, frame.payload.size)
        assertArrayEquals(bytes, frame.encode())
    }

    @Test fun frameRejectsBadPayloadSizes() {
        assertThrows(IllegalArgumentException::class.java) { AudioFrame(Channel.MIC, 0, 0, ByteArray(638)).encode() }
        assertThrows(IllegalArgumentException::class.java) { AudioFrame(Channel.MIC, 0, 0, ByteArray(6402)).encode() }
        assertThrows(IllegalArgumentException::class.java) { AudioFrame(Channel.MIC, 0, 0, ByteArray(641)).encode() }
        AudioFrame(Channel.MIC, 0, 0, ByteArray(AudioFrame.FRAME_BYTES)).encode()
    }

    @Test fun frameEncodesLargeUnsignedSeq() {
        val frame = AudioFrame(Channel.SYSTEM, 0xFFFF_FFFEL, 0x8000_0000L, ByteArray(640))
        val decoded = AudioFrame.decode(frame.encode())
        assertEquals(0xFFFF_FFFEL, decoded.seq)
        assertEquals(0x8000_0000L, decoded.tMs)
        assertEquals(Channel.SYSTEM, decoded.channel)
    }

    // endregion
}
