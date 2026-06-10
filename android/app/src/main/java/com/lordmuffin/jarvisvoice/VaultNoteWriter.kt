package com.lordmuffin.jarvisvoice

import android.content.Context
import android.net.Uri
import androidx.documentfile.provider.DocumentFile
import java.time.LocalDate
import java.time.LocalTime
import java.time.format.DateTimeFormatter

/**
 * Writes voice captures directly to a user-selected local folder via the Storage Access
 * Framework. No network required — works with any synced folder (Syncthing, etc.).
 *
 * Each capture appends a timestamped bullet to a rolling daily note:
 *   {folder}/{YYYY-MM-DD}.md  →  "- HH:MM — {transcript}\n"
 *
 * The folder URI is persisted in SharedPreferences after the user picks it once via
 * ACTION_OPEN_DOCUMENT_TREE. We take a persistable permission so it survives reboots.
 */
object VaultNoteWriter {

    const val PREF_KEY_FOLDER_URI = "vault_folder_uri"

    private val dateFmt = DateTimeFormatter.ofPattern("yyyy-MM-dd")
    private val timeFmt = DateTimeFormatter.ofPattern("HH:mm")

    /** Returns true if the user has picked a vault folder and the URI is still valid. */
    fun isConfigured(context: Context): Boolean {
        val raw = savedUri(context) ?: return false
        val tree = DocumentFile.fromTreeUri(context, Uri.parse(raw)) ?: return false
        return tree.canWrite()
    }

    /** Append `transcript` to today's daily note in the saved folder. */
    fun appendToday(context: Context, transcript: String): Result<Unit> {
        val raw = savedUri(context)
            ?: return Result.failure(Exception("No vault folder selected. Pick one in Settings."))

        return try {
            val treeUri = Uri.parse(raw)
            val folder  = DocumentFile.fromTreeUri(context, treeUri)
                ?: return Result.failure(Exception("Cannot open vault folder."))

            val today    = LocalDate.now().format(dateFmt)
            val time     = LocalTime.now().format(timeFmt)
            val fileName = "$today.md"

            val note = folder.findFile(fileName)
                ?: folder.createFile("text/markdown", today)
                ?: return Result.failure(Exception("Cannot create $fileName in vault folder."))

            // "wa" = write-append; creates if missing, appends if existing
            val isNew = note.length() == 0L
            context.contentResolver.openOutputStream(note.uri, "wa")?.use { out ->
                if (isNew) {
                    out.write("# $today\n\n".toByteArray())
                }
                out.write("- $time — $transcript\n".toByteArray())
            } ?: return Result.failure(Exception("Cannot open file for writing."))

            Result.success(Unit)
        } catch (e: Exception) {
            Result.failure(e)
        }
    }

    /** Persist the folder URI and take a persistable permission grant. */
    fun saveFolderUri(context: Context, uri: Uri) {
        context.contentResolver.takePersistableUriPermission(
            uri,
            android.content.Intent.FLAG_GRANT_READ_URI_PERMISSION or
            android.content.Intent.FLAG_GRANT_WRITE_URI_PERMISSION
        )
        context.getSharedPreferences(VoiceOverlayService.PREF_FILE, Context.MODE_PRIVATE)
            .edit().putString(PREF_KEY_FOLDER_URI, uri.toString()).apply()
    }

    /** Human-readable path for display (best-effort — SAF URIs aren't pretty). */
    fun displayPath(context: Context): String {
        val raw = savedUri(context) ?: return "Not set"
        val tree = DocumentFile.fromTreeUri(context, Uri.parse(raw)) ?: return "Invalid"
        return tree.name ?: raw
    }

    private fun savedUri(context: Context): String? =
        context.getSharedPreferences(VoiceOverlayService.PREF_FILE, Context.MODE_PRIVATE)
            .getString(PREF_KEY_FOLDER_URI, null)
}
