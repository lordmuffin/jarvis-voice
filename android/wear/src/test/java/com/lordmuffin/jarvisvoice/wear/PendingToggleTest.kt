package com.lordmuffin.jarvisvoice.wear

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** The tile, the complication and a mapped button open the record screen, which toggles once it is on screen. */
class PendingToggleTest {
    @Test fun `a requested toggle runs once, when the screen is visible`() {
        val p = PendingToggle()
        p.request()
        assertTrue(p.take())
        assertFalse("a second resume must not toggle again", p.take())
    }

    @Test fun `opening the screen normally does not toggle`() {
        assertFalse(PendingToggle().take())
    }

    @Test fun `two requests before the screen shows are one toggle`() {
        val p = PendingToggle()
        p.request()
        p.request()
        assertTrue(p.take())
        assertFalse(p.take())
    }

    @Test fun `a screen rebuilt from its old intent does not replay the toggle`() {
        val p = PendingToggle()
        p.fromIntent(asked = true, restored = true)
        assertFalse("recreating the screen must not stop a running recording", p.take())
    }

    @Test fun `a fresh open from the tile toggles`() {
        val p = PendingToggle()
        p.fromIntent(asked = true, restored = false)
        assertTrue(p.take())
    }
}
