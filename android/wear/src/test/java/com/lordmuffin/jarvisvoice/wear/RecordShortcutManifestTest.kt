package com.lordmuffin.jarvisvoice.wear

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.w3c.dom.Element
import java.io.File
import javax.xml.parsers.DocumentBuilderFactory

/**
 * A watch button can only be mapped to an app in the launcher list, and it can pass that app nothing.
 * So recording needs a launcher entry of its own that lands on [ToggleActivity], not the main screen.
 */
class RecordShortcutManifestTest {
    private val androidNs = "http://schemas.android.com/apk/res/android"

    private fun xml(path: String): Element {
        val f = DocumentBuilderFactory.newInstance().apply { isNamespaceAware = true }
        return f.newDocumentBuilder().parse(File(path)).documentElement
    }

    private fun Element.children(tag: String): List<Element> {
        val nodes = getElementsByTagName(tag)
        return (0 until nodes.length).map { nodes.item(it) as Element }
    }

    private fun Element.attr(name: String) = getAttributeNS(androidNs, name)

    private fun Element.isLauncher() = children("intent-filter").any { filter ->
        filter.children("action").any { it.attr("name") == "android.intent.action.MAIN" } &&
            filter.children("category").any { it.attr("name") == "android.intent.category.LAUNCHER" }
    }

    private val manifest = xml("src/main/AndroidManifest.xml")

    private fun recordShortcut(): Element? =
        manifest.children("activity-alias").firstOrNull { it.attr("targetActivity") == ".ToggleActivity" && it.isLauncher() }

    @Test fun `a launcher entry opens straight into the record toggle`() {
        val alias = recordShortcut()
        assertNotNull("no launcher activity-alias targets .ToggleActivity", alias)
        assertEquals("true", alias!!.attr("exported"))
    }

    @Test fun `the record entry is named so it can be told apart in the button picker`() {
        val label = recordShortcut()!!.attr("label")
        assertTrue("label must be a string resource, was '$label'", label.startsWith("@string/"))
        val name = label.removePrefix("@string/")
        val text = xml("src/main/res/values/strings.xml").children("string").first { it.getAttribute("name") == name }.textContent
        assertEquals("Jarvis record", text)
    }

    @Test fun `the main screen keeps its own launcher entry`() {
        val main = manifest.children("activity").single { it.attr("name") == ".WearMainActivity" }
        assertTrue(main.isLauncher())
    }
}
