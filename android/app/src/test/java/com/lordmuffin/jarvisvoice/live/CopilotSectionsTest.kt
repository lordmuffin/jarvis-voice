package com.lordmuffin.jarvisvoice.live

import com.lordmuffin.jarvisvoice.live.CopilotSection.ACTIONS
import com.lordmuffin.jarvisvoice.live.CopilotSection.DECISIONS
import com.lordmuffin.jarvisvoice.live.CopilotSection.FINAL_NOTE
import com.lordmuffin.jarvisvoice.live.CopilotSection.NOTES
import com.lordmuffin.jarvisvoice.live.CopilotSection.RELATED
import com.lordmuffin.jarvisvoice.live.CopilotSection.SUGGESTIONS
import com.lordmuffin.jarvisvoice.live.protocol.CopilotItem
import com.lordmuffin.jarvisvoice.live.protocol.CopilotSnapshot
import com.lordmuffin.jarvisvoice.live.protocol.Related
import com.lordmuffin.jarvisvoice.live.protocol.ServerEvent
import com.lordmuffin.jarvisvoice.live.protocol.Suggestion
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** Section order everywhere: Suggestions, Actions, Decisions, Notes, Related, Final note. */
class CopilotSectionsTest {

    private val full = CopilotSnapshot(
        version = 4,
        notes = listOf(CopilotItem("n1", "Egress costs not discussed")),
        actions = listOf(CopilotItem("a1", "Send the node inventory", owner = "Andrew", due = "Fri")),
        decisions = listOf(CopilotItem("d1", "Pilot on k3s2 first")),
        suggestions = listOf(Suggestion("s1", "question", "Ask about ARM images", expiresAtMs = 60_000)),
        related = listOf(Related("vault/arc.md", "GitHub ARC on k3s", "…", "obsidian://open")),
    )
    private val note = ServerEvent.FinalNote("Meetings/arc.md", "ARC call")

    @Test fun sectionsComeInTheDesignOrder() {
        val sections = CopilotSections.build(full, note, streamMs = 10_000)
        assertEquals(listOf(SUGGESTIONS, ACTIONS, DECISIONS, NOTES, RELATED, FINAL_NOTE), sections.map { it.section })
    }

    @Test fun emptySectionsAreLeftOut() {
        val onlyNotes = full.copy(actions = emptyList(), decisions = emptyList(), suggestions = emptyList(), related = emptyList())
        assertEquals(listOf(NOTES), CopilotSections.build(onlyNotes, null, streamMs = 0).map { it.section })
        assertTrue(CopilotSections.build(null, null, streamMs = 0).isEmpty())
    }

    @Test fun expiredSuggestionsAreDropped() {
        assertEquals(1, CopilotSections.build(full, null, streamMs = 59_999).first { it.section == SUGGESTIONS }.entries.size)
        assertTrue(CopilotSections.build(full, null, streamMs = 60_000).none { it.section == SUGGESTIONS })
    }

    /** A finished session has no live stream clock, so there is nothing to suggest. */
    @Test fun noStreamClockMeansNoSuggestions() {
        assertTrue(CopilotSections.build(full, null, streamMs = null).none { it.section == SUGGESTIONS })
    }

    @Test fun actionsKeepOwnerAndDue() {
        val action = CopilotSections.build(full, null, streamMs = null).first { it.section == ACTIONS }.entries.single()
        assertEquals("Send the node inventory", action.text)
        assertEquals("Andrew", action.owner)
        assertEquals("Fri", action.due)
    }

    @Test fun suggestionsKeepTheirKind() {
        val s = CopilotSections.build(full, null, streamMs = 0).first { it.section == SUGGESTIONS }.entries.single()
        assertEquals("question", s.kind)
    }

    @Test fun relatedShowsTheTitle() {
        assertEquals("GitHub ARC on k3s", CopilotSections.build(full, null, streamMs = null).first { it.section == RELATED }.entries.single().text)
    }

    @Test fun finalNoteIsLast() {
        val last = CopilotSections.build(full, note, streamMs = null).last()
        assertEquals(FINAL_NOTE, last.section)
        assertEquals("ARC call", last.entries.single().text)
    }
}
