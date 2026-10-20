package com.lordmuffin.jarvisvoice.live.protocol

import org.json.JSONArray
import org.json.JSONObject

/** Cumulative per-channel seqs, the shape of `hello.resume` and `hello_ack.acked`. */
data class ChannelSeqs(val mic: Long? = null, val system: Long? = null) {
    operator fun get(channel: Channel): Long? = when (channel) {
        Channel.MIC -> mic
        Channel.SYSTEM -> system
    }

    fun with(channel: Channel, seq: Long?): ChannelSeqs = when (channel) {
        Channel.MIC -> copy(mic = seq)
        Channel.SYSTEM -> copy(system = seq)
    }

    fun toJson(): JSONObject = JSONObject()
        .put("mic", mic ?: JSONObject.NULL)
        .put("system", system ?: JSONObject.NULL)
}

data class DraftSegment(
    val channel: Channel,
    val startMs: Long,
    val endMs: Long,
    val text: String,
    val final: Boolean,
)

/** Text messages a producer sends over the stream WebSocket. */
sealed class ClientMessage {
    abstract fun toJson(): JSONObject

    fun encode(): String = toJson().toString()

    data class Hello(val device: String, val resume: ChannelSeqs) : ClientMessage() {
        override fun toJson(): JSONObject = JSONObject()
            .put("type", "hello")
            .put("protocol", PROTOCOL_VERSION)
            .put("device", device)
            .put("codec", CODEC)
            .put("resume", resume.toJson())
    }

    data class Draft(val draft: DraftSegment) : ClientMessage() {
        override fun toJson(): JSONObject = JSONObject()
            .put("type", "draft_segment")
            .put("channel", draft.channel.wire)
            .put("start_ms", draft.startMs)
            .put("end_ms", draft.endMs)
            .put("text", draft.text)
            .put("final", draft.final)
    }

    data class Marker(val tMs: Long, val label: String) : ClientMessage() {
        override fun toJson(): JSONObject = JSONObject()
            .put("type", "marker")
            .put("t_ms", tMs)
            .put("label", label)
    }

    data object End : ClientMessage() {
        override fun toJson(): JSONObject = JSONObject().put("type", "end")
    }

    companion object {
        const val PROTOCOL_VERSION = 1
        const val CODEC = "pcm16le_16k"
    }
}

/** REST request bodies. */
object RestBodies {
    fun sessionCreate(mode: SessionMode, channels: List<Channel>, title: String?): JSONObject =
        JSONObject().apply {
            if (!title.isNullOrBlank()) put("title", title)
            put("mode", mode.wire)
            put("channels", JSONArray(channels.map { it.wire }))
        }

    fun ticket(role: String = "producer"): JSONObject = JSONObject().put("role", role)
}
