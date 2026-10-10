package com.lordmuffin.jarvisvoice.live

import com.lordmuffin.jarvisvoice.live.protocol.SessionMode
import org.json.JSONObject
import java.io.File

/** Per-session bookkeeping stored next to the outbox as `meta.json`. */
data class SessionMeta(
    /** Outbox directory name: the server session id once known, else a local UUID. */
    val id: String,
    val mode: SessionMode,
    val title: String?,
    val startedAt: Long,
    /** Recorded with "Local only" on; never streamed live. */
    val localOnly: Boolean,
    /** Server session id; null until a local-only session is uploaded. */
    val serverId: String?,
    val state: State,
    /** Where the local Markdown transcript was written (file path or SAF uri). */
    val notePath: String? = null,
    val error: String? = null,
) {
    enum class State {
        /** Capture running (or the process died mid-session). */
        RECORDING,
        /** Capture stopped; audio still has to reach the server. */
        PENDING,
        /** The server acked everything and the session was ended. */
        DONE,
        /** The server rejected the session (ended elsewhere, deleted, auth). Audio is kept. */
        FAILED,
    }

    fun toJson(): JSONObject = JSONObject().apply {
        put("id", id)
        put("mode", mode.wire)
        put("title", title ?: JSONObject.NULL)
        put("started_at", startedAt)
        put("local_only", localOnly)
        put("server_id", serverId ?: JSONObject.NULL)
        put("state", state.name)
        put("note_path", notePath ?: JSONObject.NULL)
        put("error", error ?: JSONObject.NULL)
    }

    companion object {
        fun fromJson(o: JSONObject): SessionMeta = SessionMeta(
            id = o.getString("id"),
            mode = SessionMode.fromWire(o.getString("mode")) ?: SessionMode.SOLO,
            title = o.optStringOrNull("title"),
            startedAt = o.getLong("started_at"),
            localOnly = o.getBoolean("local_only"),
            serverId = o.optStringOrNull("server_id"),
            state = runCatching { State.valueOf(o.getString("state")) }.getOrDefault(State.PENDING),
            notePath = o.optStringOrNull("note_path"),
            error = o.optStringOrNull("error"),
        )

        private fun JSONObject.optStringOrNull(key: String): String? =
            if (!has(key) || isNull(key)) null else getString(key)
    }
}

/** The on-disk set of sessions under `<root>/<id>/`. */
class SessionRepository(val root: File) {

    fun read(id: String): SessionMeta? {
        val f = File(File(root, id), META_FILE)
        if (!f.exists()) return null
        return runCatching { SessionMeta.fromJson(JSONObject(f.readText())) }.getOrNull()
    }

    fun write(meta: SessionMeta) {
        val dir = File(root, meta.id)
        dir.mkdirs()
        Outbox.writeAtomically(File(dir, META_FILE), meta.toJson().toString())
    }

    fun update(id: String, change: (SessionMeta) -> SessionMeta): SessionMeta? =
        read(id)?.let(change)?.also { write(it) }

    /** Newest first. */
    fun list(): List<SessionMeta> =
        (root.listFiles { f -> f.isDirectory } ?: emptyArray())
            .mapNotNull { read(it.name) }
            .sortedByDescending { it.startedAt }

    /** Sessions whose audio has not fully reached the server (or failed to). */
    fun pending(activeId: String?): List<SessionMeta> =
        list().filter { it.state != SessionMeta.State.DONE && it.id != activeId }

    fun delete(id: String) {
        File(root, id).deleteRecursively()
    }

    /** Moves a closed session to a new id (local UUID → server id). */
    fun rename(meta: SessionMeta, newId: String): SessionMeta {
        Outbox.rename(root, meta.id, newId)
        return meta.copy(id = newId).also { write(it) }
    }

    companion object {
        private const val META_FILE = "meta.json"
    }
}
