package com.lordmuffin.jarvisvoice.live

import com.lordmuffin.jarvisvoice.live.protocol.Channel
import com.lordmuffin.jarvisvoice.live.protocol.ChannelSeqs
import com.lordmuffin.jarvisvoice.live.protocol.CopilotSnapshot
import com.lordmuffin.jarvisvoice.live.protocol.DraftSegment
import com.lordmuffin.jarvisvoice.live.protocol.Segment
import com.lordmuffin.jarvisvoice.live.protocol.ServerEvent
import com.lordmuffin.jarvisvoice.live.protocol.SessionMode
import com.lordmuffin.jarvisvoice.live.protocol.Speaker
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.ZoneOffset

class SessionStoreTest {

    private fun draft(start: Long, end: Long, text: String, final: Boolean = false) =
        DraftSegment(Channel.MIC, start, end, text, final)

    private fun segment(id: String, start: Long, end: Long, text: String) =
        ServerEvent.SegmentEvent(Segment(id, Channel.MIC, Speaker.ME, start, end, text, "local"))

    private fun copilot(version: Long) =
        ServerEvent.Copilot(CopilotSnapshot(version, emptyList(), emptyList(), emptyList(), emptyList(), emptyList()))

    @Test fun newerDraftReplacesOverlappingDraft() {
        val store = SessionStore()
        store.applyDraft(draft(0, 2000, "hel"))
        store.applyDraft(draft(0, 4000, "hello world", final = true))
        val lines = store.snapshot.lines
        assertEquals(listOf("hello world"), lines.map { it.text })
        assertTrue(lines.single().isDraft)
    }

    @Test fun segmentReplacesOverlappingDraftsOnly() {
        val store = SessionStore()
        store.applyDraft(draft(0, 2000, "a"))
        store.applyDraft(draft(5000, 6000, "later"))
        store.apply(segment("seg_1", 500, 2500, "A!"))
        val lines = store.snapshot.lines
        assertEquals(listOf("A!", "later"), lines.map { it.text })
        assertEquals(listOf(false, true), lines.map { it.isDraft })
    }

    @Test fun staleDraftAfterSegmentIsIgnored() {
        val store = SessionStore()
        store.apply(segment("seg_1", 0, 2000, "final"))
        store.applyDraft(draft(1000, 1500, "late draft"))
        assertEquals(listOf("final"), store.snapshot.lines.map { it.text })
    }

    @Test fun repeatedSegmentIdReplaces() {
        val store = SessionStore()
        store.apply(segment("seg_1", 0, 2000, "v1"))
        store.apply(segment("seg_1", 0, 2000, "v2"))
        assertEquals(listOf("v2"), store.snapshot.lines.map { it.text })
    }

    @Test fun highestCopilotVersionWins() {
        val store = SessionStore()
        store.apply(copilot(3))
        store.apply(copilot(2))
        assertEquals(3L, store.snapshot.copilot!!.version)
        store.apply(copilot(4))
        assertEquals(4L, store.snapshot.copilot!!.version)
    }

    @Test fun statusAndAcksUpdateSnapshot() {
        val store = SessionStore()
        store.apply(ServerEvent.Status("local", llmOk = false, lagMs = 180))
        store.apply(ServerEvent.HelloAck("x", ChannelSeqs()))
        assertEquals("local", store.snapshot.sttTier)
        assertFalse(store.snapshot.llmOk)
        assertEquals(180L, store.snapshot.lagMs)
    }

    @Test fun finalDraftsFeedTheLocalNote() {
        val store = SessionStore()
        store.applyDraft(draft(0, 2000, "partial"))
        store.applyDraft(draft(3000, 5000, "done", final = true))
        assertEquals(listOf("done"), store.finalDrafts().map { it.text })
    }

    @Test fun localMarkdownMatchesMacLayout() {
        val text = LocalMarkdown.render(
            sessionId = "abc", mode = SessionMode.SOLO, channels = listOf(Channel.MIC),
            startedAt = 1_760_000_000_000, uploadedAs = null,
            lines = listOf(LocalMarkdown.Line(Channel.MIC, 65_000, "second"), LocalMarkdown.Line(Channel.MIC, 1_000, "first")),
            markers = listOf(LocalMarkdown.Marker(30_000, "decision")),
            zone = ZoneOffset.UTC,
        )
        assertTrue(text.startsWith("---\njarvis_session: abc\nmode: solo\nchannels: mic\nstarted: 2025-10-09T08:53:20Z\nuploaded: false\n---\n"))
        assertTrue(text.contains("- `00:30` decision"))
        assertTrue(text.indexOf("**Me** `00:01` first") < text.indexOf("**Me** `01:05` second"))
        assertTrue(LocalMarkdown.markUploaded(text, "srv-1").contains("\nuploaded: srv-1\n"))
        assertEquals("2025-10-09 0853 Jarvis Live.md", LocalMarkdown.fileName(1_760_000_000_000, ZoneOffset.UTC))
    }

    @Test fun reconnectPolicyBacksOffAndCaps() {
        val policy = ReconnectPolicy()
        assertEquals(listOf(500L, 1000L, 2000L, 4000L), (0..3).map { policy.delayMs(it, random = 0.0) })
        assertEquals(30_000L, policy.delayMs(10, random = 0.0))
        assertEquals(30_000L, policy.delayMs(10, random = 1.0))
        assertEquals(625L, policy.delayMs(0, random = 1.0))
    }
}
