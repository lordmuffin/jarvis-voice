package com.lordmuffin.jarvisvoice

import android.content.ContentValues
import android.content.Context
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper

class DictationHistoryManager(context: Context) :
    SQLiteOpenHelper(context, DB_NAME, null, 1) {

    companion object {
        private const val DB_NAME = "jarvis_history.db"
        private const val TABLE  = "sessions"
    }

    override fun onCreate(db: SQLiteDatabase) {
        db.execSQL("""
            CREATE TABLE $TABLE (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp    INTEGER NOT NULL,
                transcript   TEXT    NOT NULL,
                word_count   INTEGER NOT NULL,
                duration_ms  INTEGER NOT NULL,
                wpm          REAL    NOT NULL
            )
        """.trimIndent())
    }

    override fun onUpgrade(db: SQLiteDatabase, old: Int, new: Int) {}

    fun saveSession(transcript: String, durationMs: Long): DictationSession {
        val words = transcript.trim().split(Regex("\\s+")).filter { it.isNotEmpty() }
        val wordCount = words.size
        val wpm = if (durationMs >= 1_000) wordCount * 60_000f / durationMs else 0f
        val ts = System.currentTimeMillis()
        val cv = ContentValues().apply {
            put("timestamp",   ts)
            put("transcript",  transcript)
            put("word_count",  wordCount)
            put("duration_ms", durationMs)
            put("wpm",         wpm)
        }
        val id = writableDatabase.insert(TABLE, null, cv)
        return DictationSession(id, ts, transcript, wordCount, durationMs, wpm)
    }

    fun getRecentSessions(limit: Int = 100): List<DictationSession> {
        val list = mutableListOf<DictationSession>()
        readableDatabase.rawQuery(
            "SELECT id,timestamp,transcript,word_count,duration_ms,wpm FROM $TABLE ORDER BY timestamp DESC LIMIT ?",
            arrayOf(limit.toString())
        ).use { c ->
            while (c.moveToNext()) list += DictationSession(
                c.getLong(0), c.getLong(1), c.getString(2),
                c.getInt(3), c.getLong(4), c.getFloat(5)
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
            "SELECT id,timestamp,transcript,word_count,duration_ms,wpm FROM $TABLE ORDER BY timestamp DESC LIMIT 1",
            null
        ).use { c ->
            if (c.moveToFirst()) DictationSession(
                c.getLong(0), c.getLong(1), c.getString(2),
                c.getInt(3), c.getLong(4), c.getFloat(5)
            ) else null
        }

    fun deleteSession(id: Long) {
        writableDatabase.delete(TABLE, "id=?", arrayOf(id.toString()))
    }
}
