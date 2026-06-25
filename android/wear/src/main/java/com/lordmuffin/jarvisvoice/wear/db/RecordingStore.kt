package com.lordmuffin.jarvisvoice.wear.db

import android.content.Context
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/**
 * Lightweight JSON-backed store for pending recordings.
 * Persists to filesDir/pending_recordings.json — no annotation processing required.
 */
class RecordingStore private constructor(private val storeFile: File) {

    private val _latestFlow = MutableStateFlow<RecordingEntity?>(null)
    val latestFlow: StateFlow<RecordingEntity?> = _latestFlow.asStateFlow()

    @Synchronized
    fun insert(filePath: String, timestampMs: Long): Long {
        val list = readAll().toMutableList()
        val id = if (list.isEmpty()) 1L else list.maxOf { it.id } + 1
        list.add(RecordingEntity(id = id, filePath = filePath, timestampMs = timestampMs))
        writeAll(list)
        _latestFlow.value = list.maxByOrNull { it.timestampMs }
        return id
    }

    @Synchronized
    fun getPending(): List<RecordingEntity> =
        readAll().filter { it.status == SyncStatus.PENDING }.sortedBy { it.timestampMs }

    @Synchronized
    fun markSynced(id: Long, response: String?) {
        val list = readAll().map {
            if (it.id == id) it.copy(status = SyncStatus.SYNCED, response = response) else it
        }
        writeAll(list)
        _latestFlow.value = list.maxByOrNull { it.timestampMs }
    }

    @Synchronized
    fun markFailed(id: Long, error: String) {
        val list = readAll().map {
            if (it.id == id) it.copy(status = SyncStatus.FAILED, errorMessage = error) else it
        }
        writeAll(list)
        _latestFlow.value = list.maxByOrNull { it.timestampMs }
    }

    @Synchronized
    fun pruneOldSynced(beforeMs: Long) {
        val list = readAll().filter {
            !(it.status == SyncStatus.SYNCED && it.timestampMs < beforeMs)
        }
        writeAll(list)
    }

    private fun readAll(): List<RecordingEntity> {
        if (!storeFile.exists()) return emptyList()
        return try {
            val arr = JSONArray(storeFile.readText())
            (0 until arr.length()).map { i -> arr.getJSONObject(i).toEntity() }
        } catch (_: Exception) {
            emptyList()
        }
    }

    private fun writeAll(list: List<RecordingEntity>) {
        val arr = JSONArray()
        list.forEach { arr.put(it.toJson()) }
        storeFile.writeText(arr.toString())
    }

    companion object {
        @Volatile private var INSTANCE: RecordingStore? = null

        fun get(context: Context): RecordingStore =
            INSTANCE ?: synchronized(this) {
                INSTANCE ?: RecordingStore(File(context.filesDir, "pending_recordings.json"))
                    .also { inst ->
                        INSTANCE = inst
                        inst._latestFlow.value = inst.readAll().maxByOrNull { it.timestampMs }
                    }
            }
    }
}

private fun JSONObject.toEntity() = RecordingEntity(
    id           = getLong("id"),
    filePath     = getString("filePath"),
    timestampMs  = getLong("timestampMs"),
    status       = SyncStatus.valueOf(getString("status")),
    response     = optString("response").ifBlank { null },
    errorMessage = optString("errorMessage").ifBlank { null },
)

private fun RecordingEntity.toJson() = JSONObject().apply {
    put("id",           id)
    put("filePath",     filePath)
    put("timestampMs",  timestampMs)
    put("status",       status.name)
    put("response",     response ?: "")
    put("errorMessage", errorMessage ?: "")
}
