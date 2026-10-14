package com.lordmuffin.jarvisvoice.live

import androidx.annotation.ColorRes
import com.lordmuffin.jarvisvoice.R
import org.json.JSONException
import org.json.JSONObject
import java.util.Locale

/**
 * Which Jarvis Live release channel a server is: `live` runs the latest release, `lab` the latest
 * commit on main (`server/deploy/README.md`). The Mac app's `ServerChannel`, for the same tag.
 */
sealed class ServerChannel {
    data object Live : ServerChannel()
    data object Lab : ServerChannel()

    /** Any other `JARVIS_LIVE_ENVIRONMENT`, such as a dev server's. */
    data class Other(val name: String) : ServerChannel()

    /** The tag shown beside the server's address. */
    val tag: String
        get() = when (this) {
            Live -> "LIVE"
            Lab -> "LAB"
            is Other -> name.uppercase(Locale.ROOT)
        }

    /**
     * The tag's fill, with `jv_bg` text (`design/README.md`, Color): red for live, amber for lab,
     * grey for anything else, so live is never mistaken for lab.
     */
    @get:ColorRes
    val color: Int
        get() = when (this) {
            Live -> R.color.jv_error
            Lab -> R.color.jv_warning
            is Other -> R.color.jv_text2
        }

    companion object {
        /** From the `environment` the server reports in `GET /healthz`; null when it names none. */
        fun of(environment: String?): ServerChannel? =
            when (val name = environment?.trim()?.lowercase(Locale.ROOT)) {
                null, "" -> null
                "live" -> Live
                "lab" -> Lab
                else -> Other(name)
            }

        /**
         * The channel a `GET /healthz` body names. Null unless it is an ok health check with an
         * environment: servers before the lab channel didn't say, and a captive portal's page isn't one.
         */
        fun fromHealthz(body: String): ServerChannel? = try {
            val json = JSONObject(body)
            if (json.optString("status") != "ok" || json.isNull("environment")) null
            else of(json.optString("environment"))
        } catch (e: JSONException) {
            null
        }
    }
}
