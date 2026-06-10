package com.lordmuffin.jarvisvoice

import android.content.Context
import android.os.Build
import android.os.Environment
import com.lordmuffin.jarvisvoice.speech.SttModelRegistry
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

    /** LLM model storage — survives uninstall when All Files Access is granted. */
    fun llmModelsDir(context: Context): File =
        File(dir(context), "models/llm").also { it.mkdirs() }

    /** STT model storage for the given subdir — survives uninstall when All Files Access is granted.
     *  subdir already contains the path (e.g. "models/whisper-base-en"), so we place it directly
     *  under dir(context) without adding an extra "models/stt/" prefix. */
    fun sttModelDir(context: Context, subdir: String): File =
        File(dir(context), subdir).also { it.mkdirs() }

    fun isOnExternalStorage(context: Context): Boolean {
        val extRoot = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOCUMENTS)
            ?: return false
        return dir(context).absolutePath.startsWith(extRoot.absolutePath)
    }

    fun storageLabel(context: Context): String {
        val extRoot = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOCUMENTS)
        val actualPath = if (extRoot != null) File(extRoot, DIR_NAME).absolutePath else ""
        return if (isOnExternalStorage(context)) {
            "$actualPath — survives reinstalls"
        } else {
            "Internal app storage — deleted on uninstall\n" +
            "Grant All Files Access to store data at:\n$actualPath"
        }
    }

    /** True when the app can write to external public storage. */
    fun hasExternalAccess(): Boolean =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R)
            Environment.isExternalStorageManager()
        else
            true  // WRITE_EXTERNAL_STORAGE covers API ≤ 29; legacy storage opt-out covers API 29

    /**
     * One-time migration: copies history.db, debug.log, and model files from legacy locations
     * to the current persistent paths. Safe to call on every startup.
     */
    fun migrateIfNeeded(context: Context) {
        if (hasExternalAccess()) {
            val extRoot = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOCUMENTS)
            if (extRoot != null && Environment.getExternalStorageState() == Environment.MEDIA_MOUNTED) {
                val extDir = File(extRoot, DIR_NAME).also { if (!it.exists()) it.mkdirs() }

                // DB: old SQLiteOpenHelper default path
                val oldDb = context.getDatabasePath("jarvis_history.db")
                val newDb = File(extDir, "history.db")
                if (oldDb.exists() && !newDb.exists()) {
                    runCatching {
                        oldDb.copyTo(newDb, overwrite = false)
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

        // Model migration runs regardless of external access — moves files from legacy
        // internal paths to the new unified models/ tree under dir(context).

        // LLM: old path was getExternalFilesDir()/llm_models/
        context.getExternalFilesDir(null)?.let { extFiles ->
            val oldLlm = File(extFiles, "llm_models")
            if (oldLlm.exists()) {
                val newLlm = llmModelsDir(context)
                if (oldLlm.absolutePath != newLlm.absolutePath) {
                    oldLlm.listFiles()?.forEach { src ->
                        val dst = File(newLlm, src.name)
                        if (!dst.exists()) runCatching { src.copyTo(dst) }
                    }
                }
            }
        }

        // STT: old path was filesDir/<subdir>/
        SttModelRegistry.MODELS.forEach { model ->
            val oldDir = File(context.filesDir, model.subdir)
            if (!oldDir.exists()) return@forEach
            val newDir = sttModelDir(context, model.subdir)
            if (oldDir.absolutePath == newDir.absolutePath) return@forEach
            oldDir.listFiles()?.forEach { src ->
                val dst = File(newDir, src.name)
                if (!dst.exists()) runCatching { src.copyTo(dst) }
            }
        }
    }
}
