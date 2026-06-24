package com.lordmuffin.jarvisvoice.chat

import android.content.Context
import com.lordmuffin.jarvisvoice.DebugLog
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.UUID

class SessionManager(context: Context) {

    private val dir        = File(context.filesDir, "chat_sessions").also { it.mkdirs() }
    private val indexFile  = File(dir, "index.json")
    private val legacyFile = File(context.filesDir, "voice_chat_history.json")

    // ── Public API ────────────────────────────────────────────────────────────

    fun listSessions(): List<SessionMeta> {
        maybeMigrateLegacy()
        return readIndex()
    }

    fun createSession(firstMessage: String = ""): SessionMeta {
        val id   = UUID.randomUUID().toString()
        val name = autoName(firstMessage)
        val now  = System.currentTimeMillis()
        val meta = SessionMeta(id, name, now, now, 0, "")
        val index = readIndex().toMutableList()
        index.add(0, meta)   // newest first
        writeIndex(index)
        return meta
    }

    fun loadMessages(id: String): List<ConversationMessage> {
        val file = sessionFile(id)
        if (!file.exists()) return emptyList()
        return runCatching {
            val arr = JSONArray(file.readText())
            (0 until arr.length()).map { i ->
                val o = arr.getJSONObject(i)
                ConversationMessage(o.getString("role"), o.getString("content"))
            }
        }.getOrElse {
            DebugLog.e("SessionManager", "load $id failed: ${it.message}")
            emptyList()
        }
    }

    fun saveMessages(id: String, messages: List<ConversationMessage>) {
        runCatching {
            val arr = JSONArray()
            messages.takeLast(500).forEach { msg ->
                arr.put(JSONObject().put("role", msg.role).put("content", msg.content))
            }
            sessionFile(id).writeText(arr.toString())
            updateMeta(id, messages)
        }.onFailure { DebugLog.e("SessionManager", "save $id failed: ${it.message}") }
    }

    fun deleteSession(id: String) {
        sessionFile(id).delete()
        val index = readIndex().filter { it.id != id }
        writeIndex(index)
    }

    fun renameSession(id: String, newName: String) {
        val index = readIndex().map { if (it.id == id) it.copy(name = newName) else it }
        writeIndex(index)
    }

    fun getMeta(id: String): SessionMeta? = readIndex().find { it.id == id }

    // ── Internal ──────────────────────────────────────────────────────────────

    private fun sessionFile(id: String) = File(dir, "$id.json")

    private fun readIndex(): List<SessionMeta> {
        if (!indexFile.exists()) return emptyList()
        return runCatching {
            val arr = JSONArray(indexFile.readText())
            (0 until arr.length()).map { i ->
                val o = arr.getJSONObject(i)
                SessionMeta(
                    id           = o.getString("id"),
                    name         = o.getString("name"),
                    createdAt    = o.getLong("createdAt"),
                    updatedAt    = o.getLong("updatedAt"),
                    messageCount = o.optInt("messageCount", 0),
                    preview      = o.optString("preview", ""),
                )
            }
        }.getOrElse {
            DebugLog.e("SessionManager", "readIndex failed: ${it.message}")
            emptyList()
        }
    }

    private fun writeIndex(sessions: List<SessionMeta>) {
        runCatching {
            val arr = JSONArray()
            sessions.forEach { s ->
                arr.put(JSONObject().apply {
                    put("id",           s.id)
                    put("name",         s.name)
                    put("createdAt",    s.createdAt)
                    put("updatedAt",    s.updatedAt)
                    put("messageCount", s.messageCount)
                    put("preview",      s.preview)
                })
            }
            indexFile.writeText(arr.toString())
        }.onFailure { DebugLog.e("SessionManager", "writeIndex failed: ${it.message}") }
    }

    private fun updateMeta(id: String, messages: List<ConversationMessage>) {
        val preview = messages.lastOrNull()?.content?.take(60) ?: ""
        val index   = readIndex().map { s ->
            if (s.id == id) s.copy(
                updatedAt    = System.currentTimeMillis(),
                messageCount = messages.size,
                preview      = preview,
            ) else s
        }.sortedByDescending { it.updatedAt }
        writeIndex(index)
    }

    // Import legacy single-file history as the first session on first launch.
    private fun maybeMigrateLegacy() {
        if (indexFile.exists() || !legacyFile.exists()) return
        runCatching {
            val id   = UUID.randomUUID().toString()
            val now  = System.currentTimeMillis()
            val msgs = JSONArray(legacyFile.readText())
            sessionFile(id).writeText(msgs.toString())
            val preview = runCatching {
                val last = msgs.getJSONObject(msgs.length() - 1)
                last.getString("content").take(60)
            }.getOrDefault("")
            val meta = SessionMeta(id, "Session 1", now, now, msgs.length(), preview)
            writeIndex(listOf(meta))
            legacyFile.renameTo(File(legacyFile.parent, "voice_chat_history.bak"))
            DebugLog.i("SessionManager", "migrated legacy history → session $id")
        }.onFailure { DebugLog.e("SessionManager", "migration failed: ${it.message}") }
    }

    private fun autoName(firstMessage: String): String {
        if (firstMessage.isNotBlank()) {
            val words = firstMessage.trim().split(Regex("\\s+"))
            return words.take(5).joinToString(" ").take(40)
        }
        val fmt = SimpleDateFormat("MMM d, h:mm a", Locale.getDefault())
        return fmt.format(Date())
    }
}
