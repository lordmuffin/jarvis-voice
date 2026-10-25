package com.lordmuffin.jarvisvoice.pill

import android.view.LayoutInflater
import android.view.View
import com.lordmuffin.jarvisvoice.R
import com.lordmuffin.jarvisvoice.screenshots.Display
import com.lordmuffin.jarvisvoice.screenshots.Variant
import com.lordmuffin.jarvisvoice.screenshots.paparazziFor
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test

/** The pill's size, inflated the way VoiceOverlayService does it: no parent, so no layout params. */
class PillViewTest {

    @get:Rule val paparazzi = paparazziFor(Variant(Display.COVER, dark = true))

    private val unspecified = View.MeasureSpec.makeMeasureSpec(0, View.MeasureSpec.UNSPECIFIED)

    private fun inflate() = LayoutInflater.from(paparazzi.context).inflate(R.layout.overlay_pill, null) as PillView

    private fun heightOf(state: PillState): Int {
        val pill = inflate()
        pill.render(state)
        pill.measure(unspecified, unspecified)
        return pill.measuredHeight
    }

    /** The tallest line in the listening row: the label or the timer. */
    private fun textRowHeight(): Int {
        val pill = inflate()
        return listOf(R.id.pill_label, R.id.pill_time).maxOf { id ->
            pill.findViewById<View>(id).also { it.measure(unspecified, unspecified) }.measuredHeight
        }
    }

    @Test fun idleIsAsTallAsListening() {
        assertEquals(heightOf(PillState.Listening(startedAtMs = 0, held = false)), heightOf(PillState.Idle))
    }

    @Test fun everyStateIsHalfAgainTheTextRow() {
        val expected = (textRowHeight() * 1.5f).toInt()
        for (state in listOf(PillState.Idle, PillState.Listening(startedAtMs = 0, held = false), PillState.Processing)) {
            assertEquals(state.toString(), expected, heightOf(state))
        }
    }
}
