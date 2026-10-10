package com.lordmuffin.jarvisvoice.live

import android.content.Context
import android.net.Uri
import androidx.documentfile.provider.DocumentFile
import com.lordmuffin.jarvisvoice.PersistentStorage
import com.lordmuffin.jarvisvoice.VaultNoteWriter
import com.lordmuffin.jarvisvoice.VoiceOverlayService
import com.lordmuffin.jarvisvoice.live.protocol.Channel
import com.lordmuffin.jarvisvoice.live.protocol.SessionMode
import java.io.File
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.temporal.ChronoUnit
import java.util.Locale

/**
 * The Markdown file a local-only session leaves behind — same front matter and layout as the Mac
 * app's `LocalMarkdown`, so either client's files can be uploaded and recognized later.
 */
object LocalMarkdown {
    data class Line(val channel: Channel, val startMs: Long, val text: String)
    data class Marker(val tMs: Long, val label: String)

    fun render(
        sessionId: String,
        mode: SessionMode,
        channels: List<Channel>,
        startedAt: Long,
        uploadedAs: String?,
        lines: List<Line>,
        markers: List<Marker>,
        zone: ZoneId = ZoneId.systemDefault(),
    ): String = buildString {
        append("---\n")
        append("jarvis_session: $sessionId\n")
        append("mode: ${mode.wire}\n")
        append("channels: ${channels.joinToString(",") { it.wire }}\n")
        append("started: ${DateTimeFormatter.ISO_INSTANT.format(Instant.ofEpochMilli(startedAt).truncatedTo(ChronoUnit.SECONDS))}\n")
        append("uploaded: ${uploadedAs ?: "false"}\n")
        append("---\n\n")
        append("# Jarvis Live — ${TITLE_FORMAT.format(Instant.ofEpochMilli(startedAt).atZone(zone))}\n")
        if (markers.isNotEmpty()) {
            append("\n## Markers\n\n")
            for (m in markers.sortedBy { it.tMs }) append("- `${clock(m.tMs)}` ${m.label}\n")
        }
        append("\n## Transcript (on-device draft)\n\n")
        for (line in lines.sortedWith(compareBy({ it.startMs }, { it.channel.index }))) {
            val speaker = if (line.channel == Channel.MIC) "Me" else "Them"
            append("**$speaker** `${clock(line.startMs)}` ${line.text}\n\n")
        }
    }

    fun markUploaded(text: String, serverSessionId: String): String =
        text.replace("\nuploaded: false\n", "\nuploaded: $serverSessionId\n")

    fun clock(ms: Long): String {
        val total = maxOf(0, ms) / 1000
        return String.format(Locale.US, "%02d:%02d", total / 60, total % 60)
    }

    fun fileName(startedAt: Long, zone: ZoneId = ZoneId.systemDefault()): String =
        "${FILE_FORMAT.format(Instant.ofEpochMilli(startedAt).atZone(zone))} Jarvis Live.md"

    private val FILE_FORMAT = DateTimeFormatter.ofPattern("yyyy-MM-dd HHmm", Locale.US)
    private val TITLE_FORMAT = DateTimeFormatter.ofPattern("MMM d, yyyy 'at' h:mm a", Locale.US)

    // region Storage

    /**
     * Writes the note to the Vault folder (Settings → Voice to Vault) when one is set, otherwise to
     * `Documents/JarvisVoice/Live/`. Returns the file path or SAF uri.
     */
    fun write(context: Context, fileName: String, text: String): String {
        vaultFolder(context)?.let { folder ->
            val name = fileName.removeSuffix(".md")
            val doc = folder.findFile(fileName) ?: folder.createFile("text/markdown", name)
            if (doc != null) {
                context.contentResolver.openOutputStream(doc.uri, "wt")?.use { it.write(text.toByteArray()) }
                return doc.uri.toString()
            }
        }
        val dir = File(PersistentStorage.dir(context), "Live").also { it.mkdirs() }
        val file = File(dir, fileName)
        file.writeText(text)
        return file.absolutePath
    }

    /** Rewrites `uploaded: false` in a note previously returned by [write]. */
    fun markUploaded(context: Context, path: String, serverSessionId: String) {
        runCatching {
            if (path.startsWith("content://")) {
                val uri = Uri.parse(path)
                val text = context.contentResolver.openInputStream(uri)?.use { it.readBytes().decodeToString() } ?: return
                context.contentResolver.openOutputStream(uri, "wt")?.use {
                    it.write(markUploaded(text, serverSessionId).toByteArray())
                }
            } else {
                val file = File(path)
                if (file.exists()) file.writeText(markUploaded(file.readText(), serverSessionId))
            }
        }
    }

    private fun vaultFolder(context: Context): DocumentFile? {
        val raw = context.getSharedPreferences(VoiceOverlayService.PREF_FILE, Context.MODE_PRIVATE)
            .getString(VaultNoteWriter.PREF_KEY_FOLDER_URI, null) ?: return null
        return DocumentFile.fromTreeUri(context, Uri.parse(raw))?.takeIf { it.canWrite() }
    }

    // endregion
}
