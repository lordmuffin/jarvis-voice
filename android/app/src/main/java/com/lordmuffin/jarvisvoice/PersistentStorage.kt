package com.lordmuffin.jarvisvoice

import android.content.Context
import android.os.Build
import android.os.Environment
import java.io.File

object PersistentStorage {

    private const val DIR_NAME = "JarvisVoice"

    /**
     * Returns the directory where persistent data (history DB, logs) lives.
     * On Android 11+ this is /sdcard/Documents/JarvisVoice/ when the user has
     * granted All Files Access — it survives app uninstall.
     * Falls back to internal app storage if permission is missing or SD card unavailable.
     */
    fun dir(context: Context): File {
        if (hasExternalAccess()) {
            val extRoot = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOCUMENTS)
            if (extRoot != null && Environment.getExternalStorageState() == Environment.MEDIA_MOUNTED) {
                val dir = File(extRoot, DIR_NAME)
                if (dir.exists() || dir.mkdirs()) return dir
            }
        }
        return context.filesDir
    }

    fun dbFile(context: Context): File  = File(dir(context), "history.db")
    fun logFile(context: Context): File = File(dir(context), "debug.log")

    fun isOnExternalStorage(context: Context): Boolean {
        val extRoot = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOCUMENTS)
            ?: return false
        return dir(context).absolutePath.startsWith(extRoot.absolutePath)
    }

    fun storageLabel(context: Context): String = if (isOnExternalStorage(context)) {
        "/sdcard/Documents/JarvisVoice/ — survives reinstalls"
    } else {
        "Internal app storage — deleted on uninstall (grant All Files Access to persist)"
    }

    /** True when the app can write to external public storage. */
    fun hasExternalAccess(): Boolean =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R)
            Environment.isExternalStorageManager()
        else
            true  // WRITE_EXTERNAL_STORAGE covers API ≤ 29; legacy storage opt-out covers API 29

    /**
     * One-time migration: when permission is newly granted, copies history.db and debug.log
     * from internal storage to external so existing data isn't lost.
     * Safe to call on every startup — skips silently if already migrated or no internal data.
     */
    fun migrateIfNeeded(context: Context) {
        if (!hasExternalAccess()) return
        val extRoot = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOCUMENTS)
            ?: return
        if (Environment.getExternalStorageState() != Environment.MEDIA_MOUNTED) return
        val extDir = File(extRoot, DIR_NAME).also { if (!it.exists()) it.mkdirs() }

        // DB: old SQLiteOpenHelper default path
        val oldDb = context.getDatabasePath("jarvis_history.db")
        val newDb = File(extDir, "history.db")
        if (oldDb.exists() && !newDb.exists()) {
            runCatching {
                oldDb.copyTo(newDb, overwrite = false)
                // Copy WAL/SHM journal files if present (SQLite WAL mode)
                listOf("-wal", "-shm").forEach { suffix ->
                    File("${oldDb.path}$suffix")
                        .takeIf { it.exists() }
                        ?.copyTo(File("${newDb.path}$suffix"), overwrite = true)
                }
            }
        }

        // Log
        val oldLog = File(context.filesDir, "debug.log")
        val newLog = File(extDir, "debug.log")
        if (oldLog.exists() && !newLog.exists()) {
            runCatching { oldLog.copyTo(newLog, overwrite = false) }
        }
    }
}
