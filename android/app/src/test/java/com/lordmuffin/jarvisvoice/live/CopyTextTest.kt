package com.lordmuffin.jarvisvoice.live

import com.lordmuffin.jarvisvoice.live.protocol.CopilotItem
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class CopyTextTest {

    @Test fun anActionIsAChecklistLineWithItsOwnerAndDue() {
        assertEquals("- [ ] Send the inventory (owner: Me, due: Fri)", CopyText.line("Send the inventory", "Me", "Fri"))
        assertEquals("- [ ] Book the room", CopyText.line("Book the room"))
    }

    @Test fun allActionsAreOneLineEach() {
        val items = listOf(CopilotItem("a", "One", due = "Mon"), CopilotItem("b", "Two"))
        assertEquals("- [ ] One (due: Mon)\n- [ ] Two\n", CopyText.checklist(items))
        assertNull(CopyText.checklist(emptyList()))
    }
}
