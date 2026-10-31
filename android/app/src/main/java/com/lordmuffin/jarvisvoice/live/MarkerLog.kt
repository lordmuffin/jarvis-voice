package com.lordmuffin.jarvisvoice.live

import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream

/**
 * Durable record of a session's markers, `markers.jsonl` next to the outbox (one
 * `{"t_ms","label"}` per line). Markers are tiny and rare, so every append is fsynced. The
 * connection resends all of them on each (re)connect and the server stores each once, which
 * covers both a marker sent just before a socket dropped and a process killed mid-session.
 */
class MarkerLog private constructor(private val file: File) {

    @Synchronized
    fun append(marker: LocalMarkdown.Marker) {
        val line = JSONObject().put("t_ms", marker.tMs).put("label", marker.label).toString() + "\n"
        FileOutputStream(file, true).use { out ->
            out.write(line.toByteArray())
            out.fd.sync()
        }
    }

    /** All markers in order; an unreadable (torn) line ends the list. */
    @Synchronized
    fun read(): List<LocalMarkdown.Marker> {
        if (!file.exists()) return emptyList()
        val result = ArrayList<LocalMarkdown.Marker>()
        for (line in file.readLines()) {
            val marker = runCatching {
                val o = JSONObject(line)
                LocalMarkdown.Marker(o.getLong("t_ms"), o.getString("label"))
            }.getOrNull() ?: break
            result += marker
        }
        return result
    }

    companion object {
        fun inDir(sessionDir: File) = MarkerLog(File(sessionDir, "markers.jsonl"))
    }
}
