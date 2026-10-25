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

    private fun heightOf(state: PillState): Int {
        val pill = LayoutInflater.from(paparazzi.context).inflate(R.layout.overlay_pill, null) as PillView
        pill.render(state)
        val unspecified = View.MeasureSpec.makeMeasureSpec(0, View.MeasureSpec.UNSPECIFIED)
        pill.measure(unspecified, unspecified)
        return pill.measuredHeight
    }

    @Test fun idleIsAsTallAsListening() {
        assertEquals(heightOf(PillState.Listening(startedAtMs = 0, held = false)), heightOf(PillState.Idle))
    }
}
