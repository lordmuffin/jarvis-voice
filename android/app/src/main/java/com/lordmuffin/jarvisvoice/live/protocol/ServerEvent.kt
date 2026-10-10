package com.lordmuffin.jarvisvoice.live.protocol

import org.json.JSONArray
import org.json.JSONException
import org.json.JSONObject

class ProtocolException(message: String) : Exception(message)

data class Segment(
    val id: String,
    val channel: Channel,
    val speaker: Speaker,
    val startMs: Long,
    val endMs: Long,
    val text: String,
    val sttTier: String,
)

data class CopilotItem(val id: String, val text: String, val owner: String? = null, val due: String? = null)
data class Suggestion(val id: String, val kind: String, val text: String, val expiresAtMs: Long)
data class Related(val path: String, val title: String, val snippet: String, val uri: String)

data class CopilotSnapshot(
    val version: Long,
    val notes: List<CopilotItem>,
    val actions: List<CopilotItem>,
    val decisions: List<CopilotItem>,
    val suggestions: List<Suggestion>,
    val related: List<Related>,
)

/** Messages the server sends to a producer. Relayed `draft_segment`/`marker` are not modelled. */
sealed class ServerEvent {
    data class HelloAck(val session: String, val acked: ChannelSeqs) : ServerEvent()
    data class Ack(val channel: Channel, val seq: Long) : ServerEvent()
    data class SegmentEvent(val segment: Segment) : ServerEvent()
    data class Copilot(val snapshot: CopilotSnapshot) : ServerEvent()
    data class Status(val sttTier: String?, val llmOk: Boolean, val lagMs: Long) : ServerEvent()
    data class FinalNote(val path: String, val title: String) : ServerEvent()
    data class Error(val code: String, val message: String) : ServerEvent()

    companion object {
        private val SUGGESTION_KINDS = setOf("question", "gap", "counterpoint", "fact_check")

        /** Parses one text message; returns null for types this client does not handle. */
        fun parse(text: String): ServerEvent? {
            val json = try {
                JSONObject(text)
            } catch (e: JSONException) {
                throw ProtocolException("not a JSON object")
            }
            return parse(json)
        }

        fun parse(json: JSONObject): ServerEvent? = when (json.str("type")) {
            "hello_ack" -> HelloAck(json.str("session"), json.seqs("acked"))
            "ack" -> Ack(json.channel("channel"), json.long("seq"))
            "segment" -> SegmentEvent(
                Segment(
                    id = json.str("id"),
                    channel = json.channel("channel"),
                    speaker = Speaker.fromWire(json.str("speaker"))
                        ?: throw ProtocolException("bad speaker"),
                    startMs = json.long("start_ms"),
                    endMs = json.long("end_ms"),
                    text = json.str("text"),
                    sttTier = json.str("stt_tier"),
                )
            )
            "copilot" -> Copilot(
                CopilotSnapshot(
                    version = json.long("version"),
                    notes = json.objects("notes").map { it.item() },
                    actions = json.objects("actions").map { it.item() },
                    decisions = json.objects("decisions").map { it.item() },
                    suggestions = json.objects("suggestions").map {
                        val kind = it.str("kind")
                        if (kind !in SUGGESTION_KINDS) throw ProtocolException("bad suggestion kind $kind")
                        Suggestion(it.str("id"), kind, it.str("text"), it.long("expires_at_ms"))
                    },
                    related = json.objects("related").map {
                        Related(it.str("path"), it.str("title"), it.str("snippet"), it.str("uri"))
                    },
                )
            )
            "status" -> Status(json.nullableStr("stt_tier"), json.bool("llm_ok"), json.long("lag_ms"))
            "final_note" -> FinalNote(json.str("path"), json.str("title"))
            "error" -> Error(json.str("code"), json.str("message"))
            else -> null
        }

        private fun JSONObject.item() = CopilotItem(
            id = str("id"),
            text = str("text"),
            owner = optionalStr("owner"),
            due = optionalStr("due"),
        )
    }
}

// Strict accessors: org.json's getters coerce ("7" → 7), which the schemas forbid.

internal fun JSONObject.required(key: String): Any =
    if (has(key)) get(key) else throw ProtocolException("missing $key")

internal fun JSONObject.str(key: String): String =
    required(key) as? String ?: throw ProtocolException("$key must be a string")

internal fun JSONObject.nullableStr(key: String): String? {
    val v = required(key)
    return if (v == JSONObject.NULL) null else v as? String ?: throw ProtocolException("$key must be a string")
}

internal fun JSONObject.optionalStr(key: String): String? =
    if (!has(key)) null else str(key)

internal fun JSONObject.long(key: String): Long {
    val v = required(key)
    if (v !is Int && v !is Long) throw ProtocolException("$key must be an integer")
    return (v as Number).toLong().also { if (it < 0) throw ProtocolException("$key must be >= 0") }
}

internal fun JSONObject.nullableLong(key: String): Long? {
    val v = required(key)
    return if (v == JSONObject.NULL) null else long(key)
}

internal fun JSONObject.bool(key: String): Boolean =
    required(key) as? Boolean ?: throw ProtocolException("$key must be a boolean")

internal fun JSONObject.channel(key: String): Channel =
    Channel.fromWire(str(key)) ?: throw ProtocolException("bad channel")

internal fun JSONObject.seqs(key: String): ChannelSeqs {
    val o = required(key) as? JSONObject ?: throw ProtocolException("$key must be an object")
    return ChannelSeqs(mic = o.nullableLong("mic"), system = o.nullableLong("system"))
}

internal fun JSONObject.objects(key: String): List<JSONObject> {
    val arr = required(key) as? JSONArray ?: throw ProtocolException("$key must be an array")
    return (0 until arr.length()).map {
        arr.get(it) as? JSONObject ?: throw ProtocolException("$key[$it] must be an object")
    }
}
