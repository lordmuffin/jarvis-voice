package com.lordmuffin.jarvisvoice

import android.content.ContentValues
import android.content.Context
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper

class DictationHistoryManager(context: Context) :
    SQLiteOpenHelper(context, PersistentStorage.dbFile(context).absolutePath, null, 4) {

    companion object {
        private const val TABLE = "sessions"
    }

    override fun onCreate(db: SQLiteDatabase) {
        db.execSQL("""
            CREATE TABLE $TABLE (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp       INTEGER NOT NULL,
                transcript      TEXT    NOT NULL,
                raw_transcript  TEXT    NOT NULL DEFAULT '',
                word_count      INTEGER NOT NULL,
                duration_ms     INTEGER NOT NULL,
                wpm             REAL    NOT NULL,
                llm_model       TEXT    NOT NULL DEFAULT '',
                llm_ms          INTEGER NOT NULL DEFAULT 0
            )
        """.trimIndent())
    }

    override fun onUpgrade(db: SQLiteDatabase, old: Int, new: Int) {
        if (old < 2) {
            db.execSQL("ALTER TABLE $TABLE ADD COLUMN raw_transcript TEXT NOT NULL DEFAULT ''")
        }
        if (old < 3) {
            db.execSQL("ALTER TABLE $TABLE ADD COLUMN llm_model TEXT NOT NULL DEFAULT ''")
        }
        if (old < 4) {
            db.execSQL("ALTER TABLE $TABLE ADD COLUMN llm_ms INTEGER NOT NULL DEFAULT 0")
        }
    }

    fun saveSession(rawTranscript: String, transcript: String, durationMs: Long,
                    llmModel: String = "", llmMs: Long = 0L): DictationSession {
        val words = transcript.trim().split(Regex("\\s+")).filter { it.isNotEmpty() }
        val wordCount = words.size
        val wpm = if (durationMs >= 1_000) wordCount * 60_000f / durationMs else 0f
        val ts = System.currentTimeMillis()
        val cv = ContentValues().apply {
            put("timestamp",      ts)
            put("transcript",     transcript)
            put("raw_transcript", rawTranscript)
            put("word_count",     wordCount)
            put("duration_ms",    durationMs)
            put("wpm",            wpm)
            put("llm_model",      llmModel)
            put("llm_ms",         llmMs)
        }
        val id = writableDatabase.insert(TABLE, null, cv)
        return DictationSession(id, ts, transcript, rawTranscript, wordCount, durationMs, wpm, llmModel, llmMs)
    }

    fun getRecentSessions(limit: Int = 100): List<DictationSession> {
        val list = mutableListOf<DictationSession>()
        readableDatabase.rawQuery(
            "SELECT id,timestamp,transcript,raw_transcript,word_count,duration_ms,wpm,llm_model,llm_ms FROM $TABLE ORDER BY timestamp DESC LIMIT ?",
            arrayOf(limit.toString())
        ).use { c ->
            while (c.moveToNext()) list += DictationSession(
                c.getLong(0), c.getLong(1), c.getString(2), c.getString(3),
                c.getInt(4), c.getLong(5), c.getFloat(6), c.getString(7), c.getLong(8)
            )
        }
        return list
    }

    data class LifetimeStats(val totalWords: Int, val totalSessions: Int, val avgWpm: Float)

    fun getLifetimeStats(): LifetimeStats =
        readableDatabase.rawQuery(
            "SELECT SUM(word_count),COUNT(*),AVG(wpm) FROM $TABLE", null
        ).use { c ->
            if (c.moveToFirst()) LifetimeStats(c.getInt(0), c.getInt(1), c.getFloat(2))
            else LifetimeStats(0, 0, 0f)
        }

    fun getLastSession(): DictationSession? =
        readableDatabase.rawQuery(
            "SELECT id,timestamp,transcript,raw_transcript,word_count,duration_ms,wpm,llm_model,llm_ms FROM $TABLE ORDER BY timestamp DESC LIMIT 1",
            null
        ).use { c ->
            if (c.moveToFirst()) DictationSession(
                c.getLong(0), c.getLong(1), c.getString(2), c.getString(3),
                c.getInt(4), c.getLong(5), c.getFloat(6), c.getString(7), c.getLong(8)
            ) else null
        }

    fun deleteSession(id: Long) {
        writableDatabase.delete(TABLE, "id=?", arrayOf(id.toString()))
    }
}
