package com.lordmuffin.jarvisvoice.wearcommon.inbox

import com.lordmuffin.jarvisvoice.wearcommon.AckStatus
import com.lordmuffin.jarvisvoice.wearcommon.RecordingMeta
import com.lordmuffin.jarvisvoice.wearcommon.UploadResult
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.InputStream

data class InboxEntry(val meta: RecordingMeta, val file: File)

sealed interface ReceiveResult {
    data class Stored(val meta: RecordingMeta) : ReceiveResult

    /** The phone has already received it ([done]: and uploaded it); the watch is re-sending. */
    data class AlreadyHave(val meta: RecordingMeta, val done: Boolean) : ReceiveResult

    /** The stream ended before [expected] bytes arrived. Nothing is kept and nothing is acked. */
    data class Incomplete(val expected: Long, val got: Long) : ReceiveResult

    data object BadPath : ReceiveResult
}

/**
 * Recordings that arrived from the watch and have not been uploaded yet, on the phone's disk.
 * A file is deleted only after the server acked it (see [WatchUploadProcessor]).
 */
class WatchInbox(private val dir: File) {
    private val doneFile = File(dir, "done.json")

    init { dir.mkdirs() }

    @Synchronized fun isDone(id: String) = id in doneIds()

    @Synchronized fun isPending(id: String) = fileFor(id).exists()

    @Synchronized fun pending(): List<InboxEntry> =
        dir.listFiles { f -> f.isFile && f.name.endsWith(".meta.json") }.orEmpty().mapNotNull { m ->
            val meta = runCatching { fromJson(JSONObject(m.readText())) }.getOrNull() ?: return@mapNotNull null
            val f = fileFor(meta.id)
            if (f.exists()) InboxEntry(meta, f) else null
        }.sortedBy { it.meta.startedAtMs }

    /** Copies [input] to a temp file; commits it only if exactly [RecordingMeta.bytes] arrived. */
    fun receive(path: String, input: InputStream): ReceiveResult {
        val meta = RecordingMeta.fromPath(path) ?: return ReceiveResult.BadPath
        val have = synchronized(this) {
            when {
                isDone(meta.id) -> ReceiveResult.AlreadyHave(meta, done = true)
                isPending(meta.id) -> ReceiveResult.AlreadyHave(meta, done = false)
                else -> null
            }
        }
        if (have != null) {
            // Read it out and discard it: a sender writing into a stream nobody reads would stall
            // instead of learning that we already have the file.
            drain(input)
            return have
        }
        val tmp = File(dir, "${meta.id}.part")
        val got = try {
            tmp.outputStream().use { out -> input.copyTo(out) }
        } catch (e: java.io.IOException) {
            tmp.delete()
            return ReceiveResult.Incomplete(meta.bytes, 0)
        }
        if (got != meta.bytes) {
            tmp.delete()
            return ReceiveResult.Incomplete(meta.bytes, got)
        }
        synchronized(this) {
            val dest = fileFor(meta.id)
            if (!tmp.renameTo(dest)) { tmp.delete(); return ReceiveResult.Incomplete(meta.bytes, got) }
            File(dir, "${meta.id}.meta.json").writeText(toJson(meta).toString())
        }
        return ReceiveResult.Stored(meta)
    }

    private fun drain(input: InputStream) {
        val buf = ByteArray(8 * 1024)
        try {
            while (input.read(buf) >= 0) Unit
        } catch (_: java.io.IOException) {
            // The sender gave up; nothing to keep.
        }
    }

    /** The server acked: record the id (so a re-send is recognised) and free the audio. */
    @Synchronized fun markDone(id: String) {
        val ids = (doneIds() + id).toList().takeLast(MAX_DONE)
        doneFile.writeText(JSONArray(ids).toString())
        fileFor(id).delete()
        File(dir, "$id.meta.json").delete()
    }

    /** The server refused the file: keep it for inspection, out of the upload queue. */
    @Synchronized fun quarantine(id: String) {
        val q = File(dir, "quarantine").also { it.mkdirs() }
        fileFor(id).renameTo(File(q, "$id.${EXT}"))
        File(dir, "$id.meta.json").renameTo(File(q, "$id.meta.json"))
    }

    private fun fileFor(id: String) = File(dir, "$id.$EXT")

    private fun doneIds(): Set<String> = runCatching {
        val a = JSONArray(doneFile.readText()); (0 until a.length()).map { a.getString(it) }.toSet()
    }.getOrDefault(emptySet())

    private fun toJson(m: RecordingMeta) =
        JSONObject().put("id", m.id).put("t", m.startedAtMs).put("d", m.durationMs).put("b", m.bytes)

    private fun fromJson(o: JSONObject) = RecordingMeta(o.getString("id"), o.getLong("t"), o.getLong("d"), o.getLong("b"))

    private companion object {
        const val EXT = "aac"
        const val MAX_DONE = 500
    }
}

/** Uploads what is in the inbox. The ack goes to the watch only after the server's 2xx. */
class WatchUploadProcessor(
    private val inbox: WatchInbox,
    private val upload: suspend (InboxEntry) -> UploadResult,
    private val ack: suspend (id: String, status: AckStatus) -> Unit,
) {
    enum class Outcome { DONE, RETRY_LATER, AUTH_BLOCKED }

    suspend fun processAll(): Outcome {
        for (entry in inbox.pending()) {
            when (upload(entry)) {
                is UploadResult.Acked -> {
                    ack(entry.meta.id, AckStatus.ACKED)   // if this throws the file stays: retry re-acks
                    inbox.markDone(entry.meta.id)
                }
                is UploadResult.Retry -> return Outcome.RETRY_LATER
                is UploadResult.AuthRejected -> return Outcome.AUTH_BLOCKED
                is UploadResult.Rejected -> {
                    ack(entry.meta.id, AckStatus.REJECTED)
                    inbox.quarantine(entry.meta.id)
                }
            }
        }
        return Outcome.DONE
    }
}
