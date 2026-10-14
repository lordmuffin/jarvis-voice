package com.lordmuffin.jarvisvoice.ui

import android.content.Context
import androidx.appcompat.app.AppCompatDelegate
import com.lordmuffin.jarvisvoice.VoiceOverlayService

/**
 * The app's appearance, chosen in Settings → Appearance. [SYSTEM] follows the device's day/night
 * setting; the other two override it. The colours themselves are Instrument's `inst_*` resources,
 * which switch with the night qualifier.
 */
enum class ThemeMode(val stored: String, val nightMode: Int) {
    SYSTEM("system", AppCompatDelegate.MODE_NIGHT_FOLLOW_SYSTEM),
    LIGHT("light", AppCompatDelegate.MODE_NIGHT_NO),
    DARK("dark", AppCompatDelegate.MODE_NIGHT_YES);

    companion object {
        const val PREF_KEY = "theme_mode"

        fun fromStored(value: String?): ThemeMode = entries.firstOrNull { it.stored == value } ?: SYSTEM

        fun load(context: Context): ThemeMode =
            fromStored(context.getSharedPreferences(VoiceOverlayService.PREF_FILE, Context.MODE_PRIVATE).getString(PREF_KEY, null))

        /** Saves [mode] and applies it, which recreates the running activities in the new theme. */
        fun save(context: Context, mode: ThemeMode) {
            context.getSharedPreferences(VoiceOverlayService.PREF_FILE, Context.MODE_PRIVATE)
                .edit().putString(PREF_KEY, mode.stored).apply()
            apply(mode)
        }

        fun apply(mode: ThemeMode) = AppCompatDelegate.setDefaultNightMode(mode.nightMode)
    }
}
