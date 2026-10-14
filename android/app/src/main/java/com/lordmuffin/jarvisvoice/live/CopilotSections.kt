package com.lordmuffin.jarvisvoice.live

import com.lordmuffin.jarvisvoice.live.protocol.CopilotSnapshot
import com.lordmuffin.jarvisvoice.live.protocol.ServerEvent

/** Copilot sections in the order the design fixes for every surface. */
enum class CopilotSection { SUGGESTIONS, ACTIONS, DECISIONS, NOTES, RELATED, FINAL_NOTE }

/** One line in a section. [kind] is a suggestion's kind (`question`, `gap`, …). */
data class CopilotEntry(
    val text: String,
    val owner: String? = null,
    val due: String? = null,
    val kind: String? = null,
)

data class CopilotSectionContent(val section: CopilotSection, val entries: List<CopilotEntry>)

object CopilotSections {
    /**
     * "Order everywhere: Suggestions, Actions, Decisions, Notes, Related, Final note." Empty sections are
     * omitted. [streamMs] is the session clock for expiring suggestions; null (a finished session) shows none.
     */
    fun build(snapshot: CopilotSnapshot?, finalNote: ServerEvent.FinalNote?, streamMs: Long?): List<CopilotSectionContent> {
        val out = ArrayList<CopilotSectionContent>()
        fun add(section: CopilotSection, entries: List<CopilotEntry>) {
            if (entries.isNotEmpty()) out += CopilotSectionContent(section, entries)
        }
        if (snapshot != null) {
            add(CopilotSection.SUGGESTIONS, if (streamMs == null) emptyList() else
                snapshot.suggestions.filter { it.expiresAtMs > streamMs }.map { CopilotEntry(it.text, kind = it.kind) })
            add(CopilotSection.ACTIONS, snapshot.actions.map { CopilotEntry(it.text, owner = it.owner, due = it.due) })
            add(CopilotSection.DECISIONS, snapshot.decisions.map { CopilotEntry(it.text) })
            add(CopilotSection.NOTES, snapshot.notes.map { CopilotEntry(it.text) })
            add(CopilotSection.RELATED, snapshot.related.map { CopilotEntry(it.title) })
        }
        if (finalNote != null) add(CopilotSection.FINAL_NOTE, listOf(CopilotEntry(finalNote.title)))
        return out
    }

    /** Past sessions carry only notes, actions and decisions. */
    fun build(copilot: RemoteCopilot): List<CopilotSectionContent> = build(
        CopilotSnapshot(copilot.version, copilot.notes, copilot.actions, copilot.decisions, emptyList(), emptyList()),
        finalNote = null, streamMs = null,
    )
}
