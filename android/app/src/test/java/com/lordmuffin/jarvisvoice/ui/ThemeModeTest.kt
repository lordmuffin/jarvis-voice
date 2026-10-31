package com.lordmuffin.jarvisvoice.ui

import androidx.appcompat.app.AppCompatDelegate
import org.junit.Assert.assertEquals
import org.junit.Test

/** The Settings → Appearance choice: System / Light / Dark. */
class ThemeModeTest {

    @Test fun mapsToAppCompatNightModes() {
        assertEquals(AppCompatDelegate.MODE_NIGHT_FOLLOW_SYSTEM, ThemeMode.SYSTEM.nightMode)
        assertEquals(AppCompatDelegate.MODE_NIGHT_NO, ThemeMode.LIGHT.nightMode)
        assertEquals(AppCompatDelegate.MODE_NIGHT_YES, ThemeMode.DARK.nightMode)
    }

    @Test fun storedValueRoundTrips() {
        for (mode in ThemeMode.entries) assertEquals(mode, ThemeMode.fromStored(mode.stored))
    }

    /** Never saved, or a value from a newer/older build: follow the system rather than force a theme. */
    @Test fun unknownOrMissingStoredValueFollowsTheSystem() {
        assertEquals(ThemeMode.SYSTEM, ThemeMode.fromStored(null))
        assertEquals(ThemeMode.SYSTEM, ThemeMode.fromStored(""))
        assertEquals(ThemeMode.SYSTEM, ThemeMode.fromStored("sepia"))
    }
}
