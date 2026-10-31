package com.lordmuffin.jarvisvoice.live

import com.lordmuffin.jarvisvoice.live.protocol.CopilotItem

/** What the session detail's copy buttons put on the clipboard. */
object CopyText {

    /** One action as a Markdown checklist line, written like the note's own Actions section. */
    fun line(text: String, owner: String? = null, due: String? = null): String {
        val extra = listOfNotNull(owner?.let { "owner: $it" }, due?.let { "due: $it" })
        return "- [ ] $text" + if (extra.isEmpty()) "" else " (${extra.joinToString(", ")})"
    }

    /** All actions, one line each; null when there are none. */
    fun checklist(items: List<CopilotItem>): String? =
        if (items.isEmpty()) null else items.joinToString("") { line(it.text, it.owner, it.due) + "\n" }
}
