package com.lordmuffin.jarvisvoice

import android.content.Context
import android.net.Uri
import androidx.documentfile.provider.DocumentFile

/**
 * The user-selected vault folder (Storage Access Framework). Record sessions write their
 * markdown notes into it (see [com.lordmuffin.jarvisvoice.live.LocalMarkdown]). No network
 * required — works with any synced folder (Syncthing, etc.).
 *
 * The folder URI is persisted in SharedPreferences after the user picks it once via
 * ACTION_OPEN_DOCUMENT_TREE. We take a persistable permission so it survives reboots.
 */
object VaultNoteWriter {

    const val PREF_KEY_FOLDER_URI = "vault_folder_uri"

    /** Returns true if the user has picked a vault folder and the URI is still valid. */
    fun isConfigured(context: Context): Boolean {
        val raw = savedUri(context) ?: return false
        val tree = DocumentFile.fromTreeUri(context, Uri.parse(raw)) ?: return false
        return tree.canWrite()
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
        val raw = savedUri(context) ?: return "—"
        val tree = DocumentFile.fromTreeUri(context, Uri.parse(raw)) ?: return "Invalid"
        return tree.name ?: raw
    }

    private fun savedUri(context: Context): String? =
        context.getSharedPreferences(VoiceOverlayService.PREF_FILE, Context.MODE_PRIVATE)
            .getString(PREF_KEY_FOLDER_URI, null)
}
