package com.lordmuffin.jarvisvoice.live.ask

import android.content.SharedPreferences

/**
 * Settings → Speak answers through. [DEVICE], the default, speaks a held ask's reply wherever the
 * phone's media audio plays, the built-in speaker included; the server's echo guard keeps it out of
 * the transcript. [HEADPHONES] is the earlier rule: closed headphones or text only. [key] is saved.
 */
enum class SpeakThrough(val key: String) {
    DEVICE("device"),
    HEADPHONES("headphones");

    companion object {
        const val PREF = "ask_speak_through"

        /** Unset (a new install or an upgrade) or unknown: [DEVICE]. */
        fun fromKey(key: String?): SpeakThrough = entries.firstOrNull { it.key == key } ?: DEVICE

        fun saved(prefs: SharedPreferences): SpeakThrough = fromKey(prefs.getString(PREF, null))

        fun save(prefs: SharedPreferences, value: SpeakThrough) = prefs.edit().putString(PREF, value.key).apply()
    }
}
