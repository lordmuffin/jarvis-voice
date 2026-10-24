package com.lordmuffin.jarvisvoice.ui.compose

import androidx.compose.ui.graphics.toArgb
import instrument.InstColors
import instrument.darkInstColors
import instrument.lightInstColors
import java.io.File
import javax.xml.parsers.DocumentBuilderFactory
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The View screens read `@color/inst_*` from XML, the Compose screens read `instrument.InstColors`.
 * Both are generated from one tokens file; this pins them together so they cannot drift apart.
 */
class TokenParityTest {

    private val byName: Map<String, (InstColors) -> androidx.compose.ui.graphics.Color> = mapOf(
        "inst_bg" to { it.bg }, "inst_surface" to { it.surface }, "inst_surface_2" to { it.surface2 },
        "inst_inverse" to { it.inverse }, "inst_on_inverse" to { it.onInverse },
        "inst_accent_on_inverse" to { it.accentOnInverse }, "inst_line" to { it.line },
        "inst_control" to { it.control }, "inst_text" to { it.text }, "inst_text_2" to { it.text2 },
        "inst_text_disabled" to { it.textDisabled }, "inst_accent" to { it.accent },
        "inst_on_accent" to { it.onAccent }, "inst_accent_tint" to { it.accentTint },
        "inst_success" to { it.success }, "inst_success_tint" to { it.successTint },
        "inst_warning" to { it.warning }, "inst_warning_tint" to { it.warningTint },
        "inst_error" to { it.error }, "inst_on_error" to { it.onError }, "inst_error_tint" to { it.errorTint },
        "inst_them" to { it.them }, "inst_them_text" to { it.themText }, "inst_on_them" to { it.onThem },
        "inst_chart_1" to { it.chart1 }, "inst_chart_2" to { it.chart2 }, "inst_chart_3" to { it.chart3 },
        "inst_chart_4" to { it.chart4 }, "inst_scrim" to { it.scrim },
    )

    private fun xml(path: String): Map<String, Int> {
        val doc = DocumentBuilderFactory.newInstance().newDocumentBuilder().parse(File(path))
        val nodes = doc.getElementsByTagName("color")
        return (0 until nodes.length).associate { i ->
            val n = nodes.item(i)
            n.attributes.getNamedItem("name").nodeValue to parseColor(n.textContent.trim())
        }
    }

    private fun parseColor(hex: String): Int {
        val v = hex.removePrefix("#").toLong(16)
        return if (hex.length == 7) (0xFF000000L or v).toInt() else v.toInt()
    }

    private fun check(path: String, compose: InstColors) {
        val res = xml(path)
        for ((name, pick) in byName) {
            val expected = res[name] ?: error("$name missing from $path")
            assertEquals("$name in $path", expected, pick(compose).toArgb())
        }
    }

    @Test fun lightMatchesXml() = check("src/instrument/android/res/values/inst_colors.xml", lightInstColors)
    @Test fun darkMatchesXml() = check("src/instrument/android/res/values-night/inst_colors.xml", darkInstColors)

    @Test fun everyXmlColourIsMapped() {
        val xmlNames = xml("src/instrument/android/res/values/inst_colors.xml").keys
        val unmapped = xmlNames - byName.keys - setOf("inst_channel_live", "inst_channel_lab", "inst_channel_other", "inst_on_channel")
        assertTrue("colours in XML but not in the Compose theme: $unmapped", unmapped.isEmpty())
    }
}
