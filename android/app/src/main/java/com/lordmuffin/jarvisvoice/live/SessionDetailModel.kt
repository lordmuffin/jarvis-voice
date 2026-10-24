package com.lordmuffin.jarvisvoice.live

import android.content.Context
import android.media.AudioAttributes
import android.media.MediaPlayer
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.DefaultLifecycleObserver
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleOwner
import com.lordmuffin.jarvisvoice.R
import com.lordmuffin.jarvisvoice.live.protocol.Speaker
import com.lordmuffin.jarvisvoice.ui.compose.FeedLine
import com.lordmuffin.jarvisvoice.ui.compose.Tone
import java.io.File
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

enum class DetailTab { SUMMARY, TRANSCRIPT, ACTIONS }

/** Either the session's note, or a sentence about why there is none yet. */
sealed interface SummaryUi {
    data class Note(val blocks: List<MarkdownModel.Block>) : SummaryUi
    data class Placeholder(val text: String) : SummaryUi
}

data class ActionItemUi(val text: String, val extra: String?)
data class ActionSectionUi(val title: String, val glyph: String, val items: List<ActionItemUi>)

sealed interface ActionsUi {
    data class Sections(val sections: List<ActionSectionUi>) : ActionsUi
    data class Placeholder(val text: String) : ActionsUi
}

data class AudioUi(
    val channel: String,
    val speaker: Speaker,
    val playing: Boolean = false,
    /** A download is running; the play button waits. */
    val busy: Boolean = false,
    val time: String = "",
    val progress: Float = 0f,
    val seekable: Boolean = false,
)

/** Everything the session detail draws. [loaded] is false until the first response arrives. */
data class DetailUi(
    val title: String = "",
    val status: String? = null,
    val statusTone: Tone = Tone.Neutral,
    val meta: String = "",
    val channel: ServerChannel? = null,
    val notice: String? = null,
    val loading: Boolean = true,
    val refreshing: Boolean = false,
    val error: String? = null,
    val tab: DetailTab = DetailTab.SUMMARY,
    val summary: SummaryUi = SummaryUi.Placeholder(""),
    val transcript: List<FeedLine> = emptyList(),
    val actions: ActionsUi = ActionsUi.Placeholder(""),
    val audio: List<AudioUi> = emptyList(),
) {
    val loaded: Boolean get() = status != null
}

/**
 * One server session: its summary note, transcript, copilot actions and recorded audio. Holds the
 * loading, error and playback state for the detail screen and for the pane beside the history list;
 * the logic is the former `SessionDetailActivity`'s, moved here unchanged so both can use it.
 */
class SessionDetailModel(
    context: Context,
    private val scope: CoroutineScope,
    private val clientProvider: () -> LiveApiClient,
    val sessionId: String,
    initialTitle: String?,
    initialTab: DetailTab = DetailTab.SUMMARY,
    private val lifecycle: Lifecycle? = null,
) : DefaultLifecycleObserver {

    private val app = context.applicationContext

    var ui by mutableStateOf(
        DetailUi(
            title = initialTitle?.takeIf { it.isNotBlank() } ?: app.getString(R.string.history_untitled),
            tab = initialTab,
        )
    )
        private set

    private var detail: RemoteSessionDetail? = null
    private var channel: ServerChannel? = null
    private var loadJob: Job? = null
    private val client: LiveApiClient by lazy(clientProvider)

    private class AudioRow(val channel: String) {
        var file: File? = null
        var downloadJob: Job? = null
        var durationMs: Int = 0
        var time: String = ""
        var progressMs: Int = 0
        var seekable: Boolean = false
        var busy: Boolean = false
    }

    private val audioRows = linkedMapOf<String, AudioRow>()
    private var player: MediaPlayer? = null
    private var playingRow: AudioRow? = null
    private var progressJob: Job? = null

    init {
        lifecycle?.addObserver(this)
    }

    override fun onStop(owner: LifecycleOwner) = stopPlayback()

    override fun onDestroy(owner: LifecycleOwner) = release()

    /** Stops playback and any download, and stops observing. Safe to call twice. */
    fun release() {
        lifecycle?.removeObserver(this)
        loadJob?.cancel()
        audioRows.values.forEach { it.downloadJob?.cancel() }
        stopPlayback()
    }

    fun selectTab(tab: DetailTab) {
        ui = ui.copy(tab = tab)
    }

    // region Loading

    fun load() {
        loadJob?.cancel()
        ui = ui.copy(error = null, loading = detail == null, refreshing = detail != null)
        loadJob = scope.launch {
            try {
                detail = client.getSession(sessionId)
                render()
                // The server's channel tag beside the meta line, once /healthz answers.
                if (channel == null) {
                    channel = client.channel()
                    if (channel != null) render()
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                val reason = when {
                    e is LiveHttpException && e.status == 401 -> app.getString(R.string.history_unauthorized)
                    e is LiveHttpException -> app.getString(R.string.detail_error, app.getString(R.string.livesess_http, e.status))
                    else -> app.getString(R.string.detail_error, e.message ?: e.javaClass.simpleName)
                }
                // With content already shown, a failed refresh keeps it; the reason shows as the notice.
                ui = if (detail == null) ui.copy(error = reason) else ui.copy(notice = reason)
            } finally {
                ui = ui.copy(loading = false, refreshing = false)
            }
        }
    }

    // endregion

    // region Rendering

    private fun render() {
        val d = detail ?: return
        val s = d.session
        renderAudio(d)
        ui = ui.copy(
            title = s.title ?: app.getString(R.string.history_untitled),
            status = SessionLabels.statusText(app, s.status, s.streaming),
            statusTone = SessionLabels.tone(s.status, s.streaming),
            meta = SessionLabels.meta(app, s),
            channel = channel,
            notice = if (s.isInProgress) app.getString(
                if (s.status == RemoteSession.STATUS_LIVE) R.string.detail_in_progress else R.string.detail_finalizing
            ) else null,
            summary = summaryOf(d),
            transcript = d.segments.map { FeedLine(it.id, LocalMarkdown.clock(it.startMs), speakerFor(it.speaker), it.text) },
            actions = actionsOf(d.copilot),
            audio = audioUi(),
        )
    }

    private fun summaryOf(d: RemoteSessionDetail): SummaryUi {
        val note = d.finalNote
        val body = note?.markdown?.let { MarkdownModel.stripFrontmatter(it) }?.takeIf { it.isNotBlank() }
        return when {
            body != null -> SummaryUi.Note(MarkdownModel.parse(body))
            note != null && note.markdown == null -> SummaryUi.Placeholder(app.getString(R.string.detail_summary_gone, note.path))
            d.session.isInProgress -> SummaryUi.Placeholder(app.getString(R.string.detail_summary_pending))
            else -> SummaryUi.Placeholder(app.getString(R.string.detail_summary_none))
        }
    }

    private fun actionsOf(copilot: RemoteCopilot): ActionsUi {
        if (copilot.isEmpty) return ActionsUi.Placeholder(app.getString(R.string.detail_actions_empty))
        val sections = mutableListOf<ActionSectionUi>()
        // Same order as every other copilot surface (CopilotSections); past sessions only have these three.
        for ((section, entries) in CopilotSections.build(copilot)) {
            val (title, glyph) = when (section) {
                CopilotSection.ACTIONS -> app.getString(R.string.copilot_section_actions) to "☐"
                CopilotSection.DECISIONS -> app.getString(R.string.copilot_section_decisions) to "✓"
                CopilotSection.NOTES -> app.getString(R.string.copilot_section_notes) to "•"
                else -> continue
            }
            sections += ActionSectionUi(
                title, glyph,
                entries.map { item ->
                    val extra = listOfNotNull(
                        item.owner?.let { app.getString(R.string.detail_owner, it) },
                        item.due?.let { app.getString(R.string.detail_due, it) },
                    )
                    ActionItemUi(item.text, extra.takeIf { it.isNotEmpty() }?.joinToString(" · "))
                },
            )
        }
        return ActionsUi.Sections(sections)
    }

    // endregion

    // region Audio

    private fun audioUi(): List<AudioUi> = audioRows.values.map { row ->
        AudioUi(
            channel = row.channel,
            speaker = speakerFor(row.channel),
            playing = playingRow === row && player?.isPlaying == true,
            busy = row.busy,
            time = row.time,
            progress = if (row.durationMs > 0) row.progressMs.toFloat() / row.durationMs else 0f,
            seekable = row.seekable,
        )
    }

    private fun publishAudio() {
        ui = ui.copy(audio = audioUi())
    }

    private fun renderAudio(d: RemoteSessionDetail) {
        val channels = d.audioChannels
        for (gone in audioRows.keys - channels.toSet()) {
            val row = audioRows.remove(gone) ?: continue
            if (playingRow === row) stopPlayback()
            row.downloadJob?.cancel()
        }
        for (channel in channels) {
            val row = audioRows.getOrPut(channel) { AudioRow(channel) }
            // A live session's recording keeps growing; fetch it fresh next time.
            if (d.session.isInProgress && playingRow !== row) row.file = null
        }
    }

    fun onPlayTapped(channel: String) {
        val row = audioRows[channel] ?: return
        val p = player
        if (p != null && playingRow === row) {
            if (p.isPlaying) {
                p.pause()
                progressJob?.cancel()
            } else {
                p.start()
                startProgress()
            }
            publishAudio()
            return
        }
        stopPlayback()
        val file = row.file
        if (file != null && file.isFile) startPlayer(row, file) else download(row)
    }

    /** [fraction] is 0..1 along the recording; only applies to the row being played. */
    fun seekTo(channel: String, fraction: Float) {
        val row = audioRows[channel] ?: return
        if (playingRow !== row) return
        val pos = (row.durationMs * fraction.coerceIn(0f, 1f)).toInt()
        row.progressMs = pos
        row.time = timeLabel(pos, row.durationMs)
        player?.seekTo(pos)
        publishAudio()
    }

    private fun download(row: AudioRow) {
        if (row.downloadJob?.isActive == true) return
        val dir = File(app.cacheDir, AUDIO_CACHE_DIR).apply { mkdirs() }
        // Recordings run to ~115 MB an hour: keep only this session's files.
        val prefix = "${safeName(sessionId)}_"
        dir.listFiles()?.filterNot { it.name.startsWith(prefix) }?.forEach { it.delete() }
        val dest = File(dir, "$prefix${row.channel}.wav")

        row.busy = true
        row.time = app.getString(R.string.detail_audio_downloading_plain)
        publishAudio()
        row.downloadJob = scope.launch {
            try {
                var lastPct = -1
                client.downloadAudio(sessionId, row.channel, dest) { done, total ->
                    if (total <= 0) return@downloadAudio
                    val pct = (done * 100 / total).toInt()
                    if (pct != lastPct) {
                        lastPct = pct
                        row.time = app.getString(R.string.detail_audio_downloading, pct)
                        publishAudio()
                    }
                }
                row.file = dest
                row.time = ""
                val started = lifecycle?.currentState?.isAtLeast(Lifecycle.State.STARTED) ?: true
                if (started && player == null) startPlayer(row, dest)
            } catch (e: CancellationException) {
                row.time = ""
                throw e
            } catch (e: LiveHttpException) {
                row.time = if (e.status == 404) app.getString(R.string.detail_audio_missing)
                else app.getString(R.string.detail_audio_failed, app.getString(R.string.livesess_http, e.status))
            } catch (e: Exception) {
                row.time = app.getString(R.string.detail_audio_failed, e.message ?: e.javaClass.simpleName)
            } finally {
                row.busy = false
                publishAudio()
            }
        }
    }

    private fun startPlayer(row: AudioRow, file: File) {
        val mp = MediaPlayer()
        try {
            mp.setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_MEDIA)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build()
            )
            mp.setDataSource(file.path)
            mp.prepare()
        } catch (e: Exception) {
            mp.release()
            file.delete()
            row.file = null
            row.time = app.getString(R.string.detail_audio_failed, e.message ?: e.javaClass.simpleName)
            publishAudio()
            return
        }
        mp.setOnCompletionListener { stopPlayback() }
        player = mp
        playingRow = row
        row.durationMs = mp.duration.coerceAtLeast(0)
        row.seekable = true
        mp.start()
        startProgress()
        publishAudio()
    }

    private fun startProgress() {
        progressJob?.cancel()
        progressJob = scope.launch {
            while (isActive) {
                val p = player ?: break
                val row = playingRow ?: break
                val pos = runCatching { p.currentPosition }.getOrDefault(0)
                row.progressMs = pos
                row.time = timeLabel(pos, row.durationMs)
                publishAudio()
                delay(500)
            }
        }
    }

    /** Releases the player and resets its row; safe to call when nothing is playing. */
    fun stopPlayback() {
        progressJob?.cancel()
        progressJob = null
        player?.let { runCatching { it.stop() }; it.release() }
        player = null
        val row = playingRow ?: return
        playingRow = null
        row.progressMs = 0
        row.seekable = false
        row.time = if (row.durationMs > 0) timeLabel(0, row.durationMs) else ""
        publishAudio()
    }

    private fun timeLabel(posMs: Int, durationMs: Int) =
        "${LocalMarkdown.clock(posMs.toLong())} / ${LocalMarkdown.clock(durationMs.toLong())}"

    // endregion

    private companion object {
        const val AUDIO_CACHE_DIR = "live_audio"
        fun safeName(id: String) = id.replace(Regex("[^A-Za-z0-9_-]"), "_")
    }
}
