package com.lordmuffin.jarvisvoice.live

import com.lordmuffin.jarvisvoice.live.protocol.CopilotItem
import org.json.JSONArray
import org.json.JSONObject
import java.time.Instant
import java.time.OffsetDateTime
import java.time.format.DateTimeParseException
import java.util.Locale

/** `GET /v1/me` */
data class RemoteUser(
    val id: String,
    val email: String?,
    val displayName: String,
    val isAdmin: Boolean,
    val deviceId: String?,
)

/** `POST /v1/auth/device-code` */
data class DeviceToken(val deviceId: String, val token: String)

data class RemoteOwner(val id: String, val displayName: String)

/** One row of `GET /v1/sessions`. */
data class RemoteSession(
    val id: String,
    val title: String?,
    val mode: String,
    val status: String,
    val channels: List<String>,
    val startedAt: Instant?,
    val endedAt: Instant?,
    val localOnly: Boolean,
    val streaming: Boolean,
    val owner: RemoteOwner?,
    /** Where it was recorded, when the uploader said: `watch` for the Wear OS app. */
    val source: String? = null,
) {
    val isFromWatch: Boolean get() = source == SOURCE_WATCH

    /** `live` or `finalizing`: the transcript and note may still change. */
    val isInProgress: Boolean get() = status == STATUS_LIVE || status == STATUS_FINALIZING

    val durationMs: Long?
        get() = if (startedAt != null && endedAt != null) maxOf(0, endedAt.toEpochMilli() - startedAt.toEpochMilli()) else null

    companion object {
        const val SOURCE_WATCH = "watch"
        const val STATUS_LIVE = "live"
        const val STATUS_FINALIZING = "finalizing"
        const val STATUS_DONE = "done"
        const val STATUS_FAILED = "failed"
    }
}

data class SessionPage(val items: List<RemoteSession>, val total: Int, val limit: Int, val offset: Int) {
    val hasMore: Boolean get() = items.isNotEmpty() && offset + items.size < total
    val nextOffset: Int get() = offset + items.size
}

data class RemoteSegment(
    val id: String,
    val channel: String,
    /** `me` or `them`. */
    val speaker: String,
    val startMs: Long,
    val endMs: Long,
    val text: String,
    val sttTier: String?,
)

data class RemoteCopilot(
    val version: Long,
    val notes: List<CopilotItem>,
    val actions: List<CopilotItem>,
    val decisions: List<CopilotItem>,
) {
    val isEmpty: Boolean get() = notes.isEmpty() && actions.isEmpty() && decisions.isEmpty()

    companion object {
        val EMPTY = RemoteCopilot(0, emptyList(), emptyList(), emptyList())
    }
}

data class RemoteMarker(val tMs: Long, val label: String)

/** The Markdown note the server wrote; [markdown] is null once the file has left the outbox. */
data class RemoteFinalNote(val path: String, val title: String, val markdown: String?)

/** `GET /v1/sessions/{id}` */
data class RemoteSessionDetail(
    val session: RemoteSession,
    val segments: List<RemoteSegment>,
    val copilot: RemoteCopilot,
    val markers: List<RemoteMarker>,
    val finalNote: RemoteFinalNote?,
    val audio: Map<String, Boolean>,
) {
    /** Channels with a recording on the server, mic first. */
    val audioChannels: List<String>
        get() = audio.filterValues { it }.keys.sortedBy { if (it == "mic") 0 else 1 }
}

/**
 * Lenient parsers for the Jarvis Live REST responses. Unlike the WebSocket protocol parsers these
 * never throw on a missing or mistyped optional field: history is display-only, so a server that
 * adds or drops a field should degrade the screen, not break it. Only a missing `id` is fatal.
 */
object RemoteSessions {

    fun parseMe(text: String): RemoteUser = parseMe(JSONObject(text))

    fun parseMe(json: JSONObject) = RemoteUser(
        id = json.getString("id"),
        email = json.stringOrNull("email"),
        displayName = json.stringOrNull("display_name") ?: json.stringOrNull("email") ?: "",
        isAdmin = json.optBoolean("is_admin", false),
        deviceId = json.stringOrNull("device_id"),
    )

    fun parseDeviceToken(text: String): DeviceToken {
        val json = JSONObject(text)
        return DeviceToken(deviceId = json.optString("device_id"), token = json.getString("token"))
    }

    fun parsePage(text: String): SessionPage = parsePage(JSONObject(text))

    fun parsePage(json: JSONObject): SessionPage {
        val items = json.objects("items").map(::parseSession)
        return SessionPage(
            items = items,
            total = json.optInt("total", items.size),
            limit = json.optInt("limit", items.size),
            offset = json.optInt("offset", 0),
        )
    }

    fun parseSession(json: JSONObject) = RemoteSession(
        id = json.getString("id"),
        title = json.stringOrNull("title")?.trim()?.takeIf { it.isNotEmpty() },
        mode = json.stringOrNull("mode") ?: "solo",
        status = json.stringOrNull("status") ?: RemoteSession.STATUS_DONE,
        channels = json.strings("channels"),
        startedAt = parseInstant(json.stringOrNull("started_at")),
        endedAt = parseInstant(json.stringOrNull("ended_at")),
        localOnly = json.optBoolean("local_only", false),
        streaming = json.optBoolean("streaming", false),
        owner = json.optJSONObject("owner")?.let {
            RemoteOwner(id = it.optString("id"), displayName = it.stringOrNull("display_name").orEmpty())
        },
        source = json.stringOrNull("source"),
    )

    fun parseDetail(text: String): RemoteSessionDetail = parseDetail(JSONObject(text))

    fun parseDetail(json: JSONObject): RemoteSessionDetail {
        val audio = mutableMapOf<String, Boolean>()
        json.optJSONObject("audio")?.let { a ->
            for (key in a.keys()) audio[key] = a.optBoolean(key, false)
        }
        return RemoteSessionDetail(
            session = parseSession(json),
            segments = json.objects("segments").map {
                RemoteSegment(
                    id = it.optString("id"),
                    channel = it.stringOrNull("channel") ?: "mic",
                    speaker = it.stringOrNull("speaker") ?: "me",
                    startMs = it.optLong("start_ms", 0),
                    endMs = it.optLong("end_ms", 0),
                    text = it.stringOrNull("text").orEmpty(),
                    sttTier = it.stringOrNull("stt_tier"),
                )
            }.sortedBy { it.startMs },
            copilot = json.optJSONObject("copilot")?.let(::parseCopilot) ?: RemoteCopilot.EMPTY,
            markers = json.objects("markers").map {
                RemoteMarker(tMs = it.optLong("t_ms", 0), label = it.stringOrNull("label").orEmpty())
            },
            finalNote = json.optJSONObject("final_note")?.let {
                RemoteFinalNote(
                    path = it.stringOrNull("path").orEmpty(),
                    title = it.stringOrNull("title").orEmpty(),
                    markdown = it.stringOrNull("markdown"),
                )
            },
            audio = audio,
        )
    }

    fun parseCopilot(json: JSONObject) = RemoteCopilot(
        version = json.optLong("version", 0),
        notes = json.objects("notes").mapNotNull(::item),
        actions = json.objects("actions").mapNotNull(::item),
        decisions = json.objects("decisions").mapNotNull(::item),
    )

    private fun item(json: JSONObject): CopilotItem? {
        val text = json.stringOrNull("text")?.trim().orEmpty()
        if (text.isEmpty()) return null
        return CopilotItem(
            id = json.optString("id"),
            text = text,
            owner = json.stringOrNull("owner")?.takeIf { it.isNotBlank() },
            due = json.stringOrNull("due")?.takeIf { it.isNotBlank() },
        )
    }

    /** ISO-8601 with an offset (`2026-10-11T09:14:03.123456+00:00` or `…Z`); null if absent or bad. */
    fun parseInstant(value: String?): Instant? {
        if (value.isNullOrBlank()) return null
        return try {
            OffsetDateTime.parse(value).toInstant()
        } catch (e: DateTimeParseException) {
            try { Instant.parse(value) } catch (e2: DateTimeParseException) { null }
        }
    }

    /** `42:10`, or `1:02:03` past an hour. */
    fun formatDuration(ms: Long): String {
        val total = maxOf(0, ms) / 1000
        val h = total / 3600
        val m = (total % 3600) / 60
        val s = total % 60
        return if (h > 0) String.format(Locale.US, "%d:%02d:%02d", h, m, s)
        else String.format(Locale.US, "%d:%02d", m, s)
    }

    // org.json's optString turns JSON null into "null"; these treat null and missing alike.

    private fun JSONObject.stringOrNull(key: String): String? =
        if (isNull(key)) null else (opt(key) as? String)

    private fun JSONObject.objects(key: String): List<JSONObject> {
        val arr: JSONArray = optJSONArray(key) ?: return emptyList()
        return (0 until arr.length()).mapNotNull { arr.optJSONObject(it) }
    }

    private fun JSONObject.strings(key: String): List<String> {
        val arr: JSONArray = optJSONArray(key) ?: return emptyList()
        return (0 until arr.length()).mapNotNull { arr.opt(it) as? String }
    }
}
