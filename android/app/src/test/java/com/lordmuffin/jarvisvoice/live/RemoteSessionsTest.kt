package com.lordmuffin.jarvisvoice.live

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Instant

class RemoteSessionsTest {

    private val page = """
        {"items": [
          {"id": "7d6c1b8e-0000-4000-8000-000000000001", "title": "Weekly sync", "mode": "meeting",
           "status": "done", "channels": ["mic", "system"],
           "started_at": "2026-10-11T09:00:00.123456+00:00", "ended_at": "2026-10-11T09:42:10.123456+00:00",
           "local_only": false, "streaming": true,
           "owner": {"id": "u1", "display_name": "Andrew"}},
          {"id": "7d6c1b8e-0000-4000-8000-000000000002", "title": null, "mode": "solo",
           "status": "live", "channels": ["mic"], "started_at": "2026-10-11T10:00:00Z", "ended_at": null,
           "local_only": false, "streaming": true, "owner": {"id": "u1", "display_name": "Andrew"}},
          {"id": "7d6c1b8e-0000-4000-8000-000000000003"}
        ], "total": 45, "limit": 20, "offset": 20}
    """.trimIndent()

    @Test fun parsesAListPage() {
        val p = RemoteSessions.parsePage(page)
        assertEquals(3, p.items.size)
        assertEquals(45, p.total)
        assertEquals(20, p.offset)
        assertEquals(23, p.nextOffset)
        assertTrue(p.hasMore)

        val a = p.items[0]
        assertEquals("Weekly sync", a.title)
        assertEquals("meeting", a.mode)
        assertEquals(listOf("mic", "system"), a.channels)
        assertEquals(Instant.parse("2026-10-11T09:00:00.123456Z"), a.startedAt)
        assertEquals((42 * 60 + 10) * 1000L, a.durationMs)
        assertEquals("Andrew", a.owner?.displayName)
        assertFalse(a.isInProgress)

        val b = p.items[1]
        assertNull(b.title)
        assertNull(b.endedAt)
        assertNull(b.durationMs)
        assertTrue(b.isInProgress)

        // Everything but the id missing: defaults, no exception.
        val c = p.items[2]
        assertNull(c.title)
        assertEquals("solo", c.mode)
        assertEquals(RemoteSession.STATUS_DONE, c.status)
        assertTrue(c.channels.isEmpty())
        assertNull(c.startedAt)
        assertNull(c.owner)
    }

    @Test fun lastPageHasNoMore() {
        val p = RemoteSessions.parsePage("""{"items": [{"id": "a"}], "total": 21, "limit": 20, "offset": 20}""")
        assertFalse(p.hasMore)
        assertFalse(RemoteSessions.parsePage("""{"items": [], "total": 0, "limit": 20, "offset": 0}""").hasMore)
    }

    @Test fun parsesADetailWithEverything() {
        val d = RemoteSessions.parseDetail(
            """
            {"id": "s1", "title": "Planning", "mode": "meeting", "status": "done",
             "channels": ["mic", "system"], "started_at": "2026-10-11T09:00:00+02:00",
             "ended_at": "2026-10-11T09:30:00+02:00", "local_only": false, "streaming": true,
             "owner": {"id": "u1", "display_name": "Andrew"},
             "segments": [
               {"id": "g2", "channel": "system", "speaker": "them", "start_ms": 5000, "end_ms": 7000,
                "text": "Sounds good.", "stt_tier": "server"},
               {"id": "g1", "channel": "mic", "speaker": "me", "start_ms": 1000, "end_ms": 4000,
                "text": "Let's ship it.", "stt_tier": null}
             ],
             "copilot": {"version": 3,
               "notes": [{"id": "n1", "text": "Ship Friday"}],
               "actions": [{"id": "a1", "text": "Write release notes", "owner": "Andrew", "due": "2026-10-16"},
                           {"id": "a2", "text": "Tag build"}],
               "decisions": [{"id": "d1", "text": "Go with plan B"}],
               "suggestions": [{"id": "x", "kind": "question", "text": "?", "expires_at_ms": 1}],
               "related": []},
             "markers": [{"t_ms": 3000, "label": "important"}],
             "final_note": {"path": "Meetings/Planning.md", "title": "Planning",
                            "markdown": "---\ntitle: Planning\n---\n# Planning\n"},
             "audio": {"mic": true, "system": false}}
            """.trimIndent()
        )
        assertEquals("Planning", d.session.title)
        assertEquals(Instant.parse("2026-10-11T07:00:00Z"), d.session.startedAt)
        assertEquals(listOf("g1", "g2"), d.segments.map { it.id })  // sorted by start
        assertEquals("me", d.segments[0].speaker)
        assertNull(d.segments[0].sttTier)
        assertEquals(3L, d.copilot.version)
        assertEquals(2, d.copilot.actions.size)
        assertEquals("Andrew", d.copilot.actions[0].owner)
        assertEquals("2026-10-16", d.copilot.actions[0].due)
        assertNull(d.copilot.actions[1].owner)
        assertEquals("Go with plan B", d.copilot.decisions.single().text)
        assertEquals("Ship Friday", d.copilot.notes.single().text)
        assertEquals(RemoteMarker(3000, "important"), d.markers.single())
        assertEquals("Meetings/Planning.md", d.finalNote?.path)
        assertTrue(d.finalNote?.markdown!!.startsWith("---"))
        assertEquals(listOf("mic"), d.audioChannels)
    }

    @Test fun parsesASparseDetail() {
        val d = RemoteSessions.parseDetail(
            """{"id": "s2", "title": "", "status": "finalizing", "segments": [], "copilot": null,
                "markers": null, "final_note": null, "audio": {"mic": false}}"""
        )
        assertNull(d.session.title)  // blank title reads as untitled
        assertTrue(d.session.isInProgress)
        assertTrue(d.segments.isEmpty())
        assertTrue(d.copilot.isEmpty)
        assertTrue(d.markers.isEmpty())
        assertNull(d.finalNote)
        assertTrue(d.audioChannels.isEmpty())
    }

    @Test fun parsesProcessing() {
        val d = RemoteSessions.parseDetail(
            """{"id": "s5", "status": "live", "can_regenerate": true,
                "processing": {"transcription": "running", "summary": "queued",
                               "started_at": "2026-10-24T09:00:00Z", "job_source": "upload"}}"""
        )
        val p = d.processing
        assertTrue(p.busy)
        assertEquals(RemoteProcessing.Activity.TRANSCRIBING, p.activity)
        assertEquals("upload", p.jobSource)
        assertEquals(Instant.parse("2026-10-24T09:00:00Z"), p.startedAt)
        assertTrue(d.canRegenerate)
    }

    @Test fun summarizingAfterTheTranscript() {
        val p = RemoteSessions.parseProcessing("""{"transcription": "idle", "summary": "running", "job_source": "regenerate"}""")
        assertTrue(p.busy)
        assertEquals(RemoteProcessing.Activity.SUMMARIZING, p.activity)
        assertFalse(p.transcriptionFailed)
    }

    @Test fun anOlderServerReadsIdleAndCannotProcess() {
        val d = RemoteSessions.parseDetail("""{"id": "s6", "status": "done"}""")
        assertFalse(d.processing.busy)
        assertNull(d.processing.activity)
        assertFalse(d.canRegenerate)
    }

    @Test fun aFailedTranscriptionIsNotBusy() {
        val p = RemoteSessions.parseProcessing("""{"transcription": "failed", "summary": "idle"}""")
        assertFalse(p.busy)
        assertNull(p.activity)
        assertTrue(p.transcriptionFailed)
    }

    @Test fun finalNoteWithoutMarkdown() {
        val d = RemoteSessions.parseDetail("""{"id": "s3", "final_note": {"path": "a.md", "title": "A", "markdown": null}}""")
        assertEquals("a.md", d.finalNote?.path)
        assertNull(d.finalNote?.markdown)
    }

    @Test fun audioChannelsPutMicFirst() {
        val d = RemoteSessions.parseDetail("""{"id": "s4", "audio": {"system": true, "mic": true}}""")
        assertEquals(listOf("mic", "system"), d.audioChannels)
    }

    @Test fun copilotSkipsBlankItems() {
        val c = RemoteSessions.parseCopilot(
            org.json.JSONObject("""{"actions": [{"id": "a", "text": " "}, {"id": "b"}, {"id": "c", "text": "Do it", "owner": ""}]}""")
        )
        assertEquals(listOf("Do it"), c.actions.map { it.text })
        assertNull(c.actions.single().owner)
        assertTrue(c.notes.isEmpty())
    }

    @Test fun parsesMe() {
        val me = RemoteSessions.parseMe(
            """{"id": "u1", "email": "a@example.com", "display_name": "Andrew", "is_admin": true, "device_id": "d1"}"""
        )
        assertEquals(RemoteUser("u1", "a@example.com", "Andrew", true, "d1"), me)
        val bare = RemoteSessions.parseMe("""{"id": "u2", "email": null, "display_name": "Sam", "is_admin": false, "device_id": null}""")
        assertNull(bare.email)
        assertNull(bare.deviceId)
    }

    @Test fun parsesInstants() {
        assertEquals(Instant.parse("2026-10-11T09:00:00Z"), RemoteSessions.parseInstant("2026-10-11T09:00:00+00:00"))
        assertEquals(Instant.parse("2026-10-11T09:00:00Z"), RemoteSessions.parseInstant("2026-10-11T09:00:00Z"))
        assertEquals(Instant.parse("2026-10-11T04:00:00Z"), RemoteSessions.parseInstant("2026-10-10T23:00:00-05:00"))
        assertNull(RemoteSessions.parseInstant("yesterday"))
        assertNull(RemoteSessions.parseInstant(null))
    }

    @Test fun formatsDurations() {
        assertEquals("0:00", RemoteSessions.formatDuration(0))
        assertEquals("0:59", RemoteSessions.formatDuration(59_999))
        assertEquals("42:10", RemoteSessions.formatDuration((42 * 60 + 10) * 1000L))
        assertEquals("1:02:03", RemoteSessions.formatDuration((3600 + 2 * 60 + 3) * 1000L))
    }

    @Test fun readsWhereASessionWasRecorded() {
        val watch = RemoteSessions.parseSession(org.json.JSONObject("""{"id": "s1", "source": "watch"}"""))
        assertEquals("watch", watch.source)
        assertTrue(watch.isFromWatch)

        // An older server sends no source, a newer one may send a value we don't know.
        val none = RemoteSessions.parseSession(org.json.JSONObject("""{"id": "s2"}"""))
        assertNull(none.source)
        assertFalse(none.isFromWatch)
        val nul = RemoteSessions.parseSession(org.json.JSONObject("""{"id": "s3", "source": null}"""))
        assertNull(nul.source)
        assertFalse(RemoteSessions.parseSession(org.json.JSONObject("""{"id": "s4", "source": "toaster"}""")).isFromWatch)
    }
}
