package com.lordmuffin.jarvisvoice.wearcommon

import java.net.URLDecoder
import java.net.URLEncoder

/** Data Layer addresses shared by the watch and phone apps. */
object WearPaths {
    /** Phone app advertises this capability (wear.xml) so the watch can find a node that can receive. */
    const val CAPABILITY_PHONE = "jarvis_phone_receiver"

    /** ChannelClient path prefix for one recording; [RecordingMeta] rides in the query string. */
    const val CHANNEL_RECORDING = "/jarvis/recording"

    /** DataItem, phone → watch: server URL and device token, so the watch can upload on its own. */
    const val CONFIG = "/jarvis/config"
    const val CONFIG_KEY_URL = "server_url"
    const val CONFIG_KEY_TOKEN = "token"

    /** DataItem, phone → watch, `/jarvis/ack/<recordingId>`: what the server said about it. */
    const val ACK_PREFIX = "/jarvis/ack/"
    const val ACK_KEY_STATUS = "status"

    fun ackPath(recordingId: String) = ACK_PREFIX + recordingId
    fun ackIdFromPath(path: String): String? =
        path.takeIf { it.startsWith(ACK_PREFIX) }?.removePrefix(ACK_PREFIX)?.takeIf { it.isNotBlank() }
}

/** The phone's verdict on a recording, sent back to the watch as a DataItem. */
enum class AckStatus(val wire: String) {
    /** The server stored it (2xx). The watch may delete its copy. */
    ACKED("acked"),

    /** The server refused the file. The watch keeps it. */
    REJECTED("rejected");

    companion object {
        fun fromWire(s: String?): AckStatus? = entries.firstOrNull { it.wire == s }
    }
}

/**
 * Describes a recording in flight. Travels in the channel path, so the receiver knows the id and
 * the exact byte count before reading a byte, and can refuse a transfer that stopped short.
 */
data class RecordingMeta(val id: String, val startedAtMs: Long, val durationMs: Long, val bytes: Long) {

    fun toPath(): String =
        "$CHANNEL?id=${enc(id)}&t=$startedAtMs&d=$durationMs&b=$bytes"

    companion object {
        private const val CHANNEL = WearPaths.CHANNEL_RECORDING

        /** Null for any path that is not a well-formed recording path. */
        fun fromPath(path: String): RecordingMeta? {
            if (!path.startsWith("$CHANNEL?")) return null
            val q = path.substringAfter('?').split('&').mapNotNull {
                val i = it.indexOf('=')
                if (i <= 0) null else it.substring(0, i) to URLDecoder.decode(it.substring(i + 1), "UTF-8")
            }.toMap()
            val id = q["id"]?.takeIf { isSafeId(it) } ?: return null
            val t = q["t"]?.toLongOrNull() ?: return null
            val d = q["d"]?.toLongOrNull() ?: return null
            val b = q["b"]?.toLongOrNull()?.takeIf { it > 0 } ?: return null
            return RecordingMeta(id, t, d, b)
        }

        /** Ids become file names and a query value: letters, digits, `-` and `_` only. */
        fun isSafeId(id: String) = id.isNotEmpty() && id.length <= 64 && id.all { it.isLetterOrDigit() || it == '-' || it == '_' }

        private fun enc(s: String) = URLEncoder.encode(s, "UTF-8")
    }
}
