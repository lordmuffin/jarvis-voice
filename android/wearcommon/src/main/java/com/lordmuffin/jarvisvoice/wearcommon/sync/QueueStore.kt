package com.lordmuffin.jarvisvoice.wearcommon.sync

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

interface QueueStore {
    fun all(): List<QueueItem>
    fun get(id: String): QueueItem? = all().firstOrNull { it.id == id }
    fun put(item: QueueItem)
    fun remove(id: String)
}

/**
 * The queue as one JSON file, replaced atomically (write a temp file, then rename) so a crash or a
 * dead battery mid-write leaves the previous complete queue, never a torn one. A file that still
 * cannot be read is kept as `<name>.corrupt` rather than overwritten; the audio files themselves are
 * the source of truth and [RecordingCatalog.recoverOrphans] re-queues any the queue forgot.
 */
class JsonQueueStore(private val file: File) : QueueStore {
    private val _version = MutableStateFlow(0L)

    /** Bumps on every change, so a screen can re-read the queue when it moves. */
    val version: StateFlow<Long> = _version.asStateFlow()

    @Synchronized override fun all(): List<QueueItem> = read()

    @Synchronized override fun put(item: QueueItem) {
        val list = read().filter { it.id != item.id } + item
        write(list)
    }

    @Synchronized override fun remove(id: String) {
        write(read().filter { it.id != id })
    }

    private fun read(): List<QueueItem> {
        if (!file.exists()) return emptyList()
        return try {
            val arr = JSONArray(file.readText())
            (0 until arr.length()).map { toItem(arr.getJSONObject(it)) }
        } catch (e: Exception) {
            file.renameTo(File(file.path + ".corrupt"))
            emptyList()
        }
    }

    private fun write(list: List<QueueItem>) {
        val arr = JSONArray()
        list.sortedBy { it.startedAtMs }.forEach { arr.put(toJson(it)) }
        file.parentFile?.mkdirs()
        _version.value++
        val tmp = File(file.path + ".tmp")
        tmp.writeText(arr.toString())
        if (!tmp.renameTo(file)) {
            file.delete()
            check(tmp.renameTo(file)) { "cannot write ${file.name}" }
        }
    }

    private fun toJson(i: QueueItem) = JSONObject()
        .put("id", i.id).put("file", i.filePath).put("started", i.startedAtMs)
        .put("duration", i.durationMs).put("bytes", i.bytes).put("state", i.state.name)
        .put("attempts", i.attempts).put("next", i.nextAttemptAtMs)
        .put("at_phone", i.atPhoneSinceMs ?: JSONObject.NULL)
        .put("error", i.lastError ?: JSONObject.NULL)

    private fun toItem(o: JSONObject) = QueueItem(
        id = o.getString("id"),
        filePath = o.getString("file"),
        startedAtMs = o.getLong("started"),
        durationMs = o.getLong("duration"),
        bytes = o.getLong("bytes"),
        // An unknown state (a newer build wrote it) is treated as unsent: re-sending is safe.
        state = runCatching { SyncState.valueOf(o.getString("state")) }.getOrDefault(SyncState.RECORDED),
        attempts = o.optInt("attempts", 0),
        nextAttemptAtMs = o.optLong("next", 0),
        atPhoneSinceMs = if (o.isNull("at_phone")) null else o.getLong("at_phone"),
        lastError = if (o.isNull("error")) null else o.getString("error"),
    )
}

object RecordingCatalog {
    const val EXT = "aac"

    /**
     * Queues recordings that are on disk but not in [store]: the app died mid-recording (raw ADTS
     * AAC stays playable when cut short), or the queue file was lost. [active] is the file being
     * recorded right now and is left alone. Returns the ids it queued.
     */
    fun recoverOrphans(store: QueueStore, dir: File, active: File?, now: Long): List<String> {
        val known = store.all().map { it.id }.toSet()
        val queued = ArrayList<String>()
        for (f in dir.listFiles { x -> x.isFile && x.extension == EXT }.orEmpty().sortedBy { it.name }) {
            if (f == active) continue
            if (f.length() == 0L) { f.delete(); continue }
            val id = f.nameWithoutExtension
            if (id in known) continue
            store.put(QueueItem(id, f.absolutePath, startedAtMs = f.lastModified().takeIf { it > 0 } ?: now, durationMs = 0, bytes = f.length()))
            queued.add(id)
        }
        return queued
    }
}
