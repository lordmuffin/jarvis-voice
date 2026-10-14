package com.lordmuffin.jarvisvoice.live

import android.content.Context
import android.content.Intent
import android.graphics.Typeface
import android.media.AudioAttributes
import android.media.MediaPlayer
import android.os.Bundle
import android.text.SpannableStringBuilder
import android.text.Spanned
import android.text.style.ForegroundColorSpan
import android.text.style.RelativeSizeSpan
import android.text.style.StyleSpan
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.ScrollView
import android.widget.SeekBar
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.DiffUtil
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.ListAdapter
import androidx.recyclerview.widget.RecyclerView
import com.lordmuffin.jarvisvoice.R
import com.lordmuffin.jarvisvoice.live.protocol.Speaker
import com.lordmuffin.jarvisvoice.ui.InstToast
import com.lordmuffin.jarvisvoice.ui.bindSpeaker
import java.io.File
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

/** One server session: summary note, transcript, copilot actions, and its recorded audio. */
class SessionDetailActivity : AppCompatActivity() {

    enum class Tab { SUMMARY, TRANSCRIPT, ACTIONS }

    private lateinit var settings: LiveSettings
    private lateinit var sessionId: String

    private lateinit var tvTitle: TextView
    private lateinit var tvStatus: TextView
    private lateinit var tvMeta: TextView
    private lateinit var groupNotice: View
    private lateinit var tvNotice: TextView
    private lateinit var audioList: LinearLayout
    private lateinit var groupTabs: View
    private lateinit var tabButtons: Map<Tab, Button>
    private lateinit var svText: ScrollView
    private lateinit var tvText: TextView
    private lateinit var rvTranscript: RecyclerView
    private lateinit var pbDetail: ProgressBar
    private lateinit var groupError: View
    private lateinit var tvError: TextView

    private val segmentAdapter = SegmentAdapter()
    private var tab = Tab.SUMMARY
    private var detail: RemoteSessionDetail? = null
    private var loadJob: Job? = null
    /** One client (and OkHttp connection pool) for this screen. */
    private val client: LiveApiClient by lazy { settings.apiClient() }

    // region Audio state

    private class AudioRow(
        val channel: String,
        val view: View,
        val button: Button,
        val label: TextView,
        val time: TextView,
        val seek: SeekBar,
    ) {
        var file: File? = null
        var downloadJob: Job? = null
        var durationMs: Int = 0
    }

    private val audioRows = linkedMapOf<String, AudioRow>()
    private var player: MediaPlayer? = null
    private var playingRow: AudioRow? = null
    private var progressJob: Job? = null

    // endregion

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_session_detail)
        settings = LiveSettings(this)
        sessionId = intent.getStringExtra(EXTRA_ID) ?: run { finish(); return }
        tab = savedInstanceState?.getString(STATE_TAB)?.let { runCatching { Tab.valueOf(it) }.getOrNull() } ?: Tab.SUMMARY

        tvTitle = findViewById(R.id.tv_detail_title)
        tvStatus = findViewById(R.id.tv_detail_status)
        tvMeta = findViewById(R.id.tv_detail_meta)
        groupNotice = findViewById(R.id.group_detail_notice)
        tvNotice = findViewById(R.id.tv_detail_notice)
        audioList = findViewById(R.id.list_detail_audio)
        groupTabs = findViewById(R.id.group_detail_tabs)
        svText = findViewById(R.id.sv_detail_text)
        tvText = findViewById(R.id.tv_detail_text)
        rvTranscript = findViewById(R.id.rv_detail_transcript)
        pbDetail = findViewById(R.id.pb_detail)
        groupError = findViewById(R.id.group_detail_error)
        tvError = findViewById(R.id.tv_detail_error)
        tabButtons = mapOf(
            Tab.SUMMARY to findViewById<Button>(R.id.btn_tab_summary),
            Tab.TRANSCRIPT to findViewById<Button>(R.id.btn_tab_transcript),
            Tab.ACTIONS to findViewById<Button>(R.id.btn_tab_actions),
        )

        tvTitle.text = intent.getStringExtra(EXTRA_TITLE)?.takeIf { it.isNotBlank() } ?: getString(R.string.history_untitled)
        rvTranscript.layoutManager = LinearLayoutManager(this)
        rvTranscript.adapter = segmentAdapter
        tabButtons.forEach { (t, b) -> b.setOnClickListener { selectTab(t) } }
        findViewById<Button>(R.id.btn_detail_refresh).setOnClickListener { load() }
        findViewById<Button>(R.id.btn_detail_retry).setOnClickListener { load() }

        load()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        outState.putString(STATE_TAB, tab.name)
    }

    override fun onStop() {
        super.onStop()
        stopPlayback()
    }

    override fun onDestroy() {
        stopPlayback()
        super.onDestroy()
    }

    // region Loading

    private fun load() {
        loadJob?.cancel()
        groupError.visibility = View.GONE
        if (detail == null) pbDetail.visibility = View.VISIBLE
        loadJob = lifecycleScope.launch {
            try {
                detail = client.getSession(sessionId)
                render()
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                val reason = when {
                    e is LiveHttpException && e.status == 401 -> getString(R.string.history_unauthorized)
                    e is LiveHttpException -> getString(R.string.detail_error, getString(R.string.livesess_http, e.status))
                    else -> getString(R.string.detail_error, e.message ?: e.javaClass.simpleName)
                }
                if (detail == null) {
                    tvError.text = reason
                    groupError.visibility = View.VISIBLE
                } else {
                    InstToast.show(this@SessionDetailActivity, reason)
                }
            } finally {
                pbDetail.visibility = View.GONE
            }
        }
    }

    // endregion

    // region Rendering

    private fun render() {
        val d = detail ?: return
        val s = d.session
        tvTitle.text = s.title ?: getString(R.string.history_untitled)
        SessionLabels.bindStatus(tvStatus, s.status, s.streaming)
        tvStatus.visibility = View.VISIBLE
        tvMeta.text = SessionLabels.meta(this, s)

        groupNotice.visibility = if (s.isInProgress) View.VISIBLE else View.GONE
        tvNotice.text = getString(
            if (s.status == RemoteSession.STATUS_LIVE) R.string.detail_in_progress else R.string.detail_finalizing
        )

        renderAudio(d)
        if (wide) {
            groupTabs.visibility = View.GONE
            renderPanes(d)
        } else {
            groupTabs.visibility = View.VISIBLE
            selectTab(tab)
        }
    }

    /** Unfolded: summary and actions on the left, the transcript beside the recordings on the right. */
    private fun renderPanes(d: RemoteSessionDetail) {
        val left = SpannableStringBuilder()
        for (part in listOf(summaryText(d), actionsText(d.copilot))) {
            if (part is Placeholder) continue
            if (left.isNotEmpty()) left.append("\n\n")
            left.append(part as CharSequence)
        }
        val summary = summaryText(d)
        if (left.isEmpty() && summary is Placeholder) left.append(summary.text)
        tvText.text = left
        tvText.setTextColor(getColor(R.color.inst_text))
        svText.visibility = View.VISIBLE
        val empty = d.segments.isEmpty()
        findViewById<View?>(R.id.tv_detail_transcript_empty)?.visibility = if (empty) View.VISIBLE else View.GONE
        rvTranscript.visibility = if (empty) View.GONE else View.VISIBLE
        segmentAdapter.submitList(d.segments)
    }

    private val wide by lazy { resources.getBoolean(R.bool.is_wide) }

    private fun selectTab(t: Tab) {
        tab = t
        tabButtons.forEach { (key, button) ->
            val selected = key == t
            button.isSelected = selected
        }
        val d = detail ?: return
        when (t) {
            Tab.SUMMARY -> showText(summaryText(d))
            Tab.TRANSCRIPT -> {
                if (d.segments.isEmpty()) {
                    showText(Placeholder(getString(R.string.detail_transcript_empty)))
                } else {
                    svText.visibility = View.GONE
                    rvTranscript.visibility = View.VISIBLE
                    segmentAdapter.submitList(d.segments)
                }
            }
            Tab.ACTIONS -> showText(actionsText(d.copilot))
        }
    }

    /** Marks text that is a message about missing content rather than the content itself. */
    private class Placeholder(val text: String)

    private fun showText(content: Any) {
        rvTranscript.visibility = View.GONE
        svText.visibility = View.VISIBLE
        if (content is Placeholder) {
            tvText.text = content.text
            tvText.setTextColor(getColor(R.color.inst_text_2))
        } else {
            tvText.text = content as CharSequence
            tvText.setTextColor(getColor(R.color.inst_text))
        }
        svText.scrollTo(0, 0)
    }

    private fun summaryText(d: RemoteSessionDetail): Any {
        val note = d.finalNote
        val body = note?.markdown?.let { MarkdownModel.stripFrontmatter(it) }?.takeIf { it.isNotBlank() }
        return when {
            body != null -> MarkdownSpans.render(
                body,
                accentColor = getColor(R.color.inst_accent),
                mutedColor = getColor(R.color.inst_text_2),
                indentPx = dp(16),
            )
            note != null && note.markdown == null -> Placeholder(getString(R.string.detail_summary_gone, note.path))
            d.session.isInProgress -> Placeholder(getString(R.string.detail_summary_pending))
            else -> Placeholder(getString(R.string.detail_summary_none))
        }
    }

    private fun actionsText(copilot: RemoteCopilot): Any {
        if (copilot.isEmpty) return Placeholder(getString(R.string.detail_actions_empty))
        val sb = SpannableStringBuilder()
        val accent = getColor(R.color.inst_accent)
        val muted = getColor(R.color.inst_text_2)

        // Same order as every other copilot surface (CopilotSections); past sessions only have these three.
        for ((section, entries) in CopilotSections.build(copilot)) {
            val (title, glyph) = when (section) {
                CopilotSection.ACTIONS -> getString(R.string.copilot_section_actions) to "☐"
                CopilotSection.DECISIONS -> getString(R.string.copilot_section_decisions) to "✓"
                CopilotSection.NOTES -> getString(R.string.copilot_section_notes) to "•"
                else -> continue
            }
            if (sb.isNotEmpty()) sb.append("\n\n")
            val start = sb.length
            sb.append(title.uppercase())
            sb.setSpan(ForegroundColorSpan(muted), start, sb.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
            sb.setSpan(RelativeSizeSpan(0.8f), start, sb.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
            sb.setSpan(StyleSpan(Typeface.BOLD), start, sb.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
            for (item in entries) {
                sb.append('\n')
                val g = sb.length
                sb.append(glyph).append("  ")
                sb.setSpan(ForegroundColorSpan(accent), g, g + glyph.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
                sb.append(item.text)
                val extra = listOfNotNull(
                    item.owner?.let { getString(R.string.detail_owner, it) },
                    item.due?.let { getString(R.string.detail_due, it) },
                )
                if (extra.isNotEmpty()) {
                    sb.append("\n    ")
                    val e = sb.length
                    sb.append(extra.joinToString(" · "))
                    sb.setSpan(ForegroundColorSpan(muted), e, sb.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
                    sb.setSpan(RelativeSizeSpan(0.85f), e, sb.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
                }
            }
        }
        return sb
    }

    private fun dp(value: Int): Int = (value * resources.displayMetrics.density).toInt()

    // endregion

    // region Audio

    private fun renderAudio(d: RemoteSessionDetail) {
        val channels = d.audioChannels
        for (gone in audioRows.keys - channels.toSet()) {
            val row = audioRows.remove(gone) ?: continue
            if (playingRow === row) stopPlayback()
            row.downloadJob?.cancel()
            audioList.removeView(row.view)
        }
        val inflater = LayoutInflater.from(this)
        for (channel in channels) {
            val row = audioRows.getOrPut(channel) {
                val view = inflater.inflate(R.layout.item_audio_channel, audioList, false)
                AudioRow(
                    channel = channel,
                    view = view,
                    button = view.findViewById(R.id.btn_audio_play),
                    label = view.findViewById(R.id.tv_audio_label),
                    time = view.findViewById(R.id.tv_audio_time),
                    seek = view.findViewById(R.id.sb_audio),
                ).also { row ->
                    row.label.text = getString(if (speakerFor(channel) == Speaker.ME) R.string.detail_audio_mic else R.string.detail_audio_system)
                    row.button.setOnClickListener { onPlayTapped(row) }
                    row.seek.setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
                        override fun onProgressChanged(seekBar: SeekBar, progress: Int, fromUser: Boolean) {
                            if (fromUser) row.time.text = timeLabel(progress, row.durationMs)
                        }
                        override fun onStartTrackingTouch(seekBar: SeekBar) {}
                        override fun onStopTrackingTouch(seekBar: SeekBar) {
                            if (playingRow === row) player?.seekTo(seekBar.progress)
                        }
                    })
                    audioList.addView(view)
                }
            }
            // A live session's recording keeps growing; fetch it fresh next time.
            if (d.session.isInProgress && playingRow !== row) row.file = null
        }
        audioList.visibility = if (channels.isEmpty()) View.GONE else View.VISIBLE
    }

    private fun onPlayTapped(row: AudioRow) {
        val p = player
        if (p != null && playingRow === row) {
            if (p.isPlaying) {
                p.pause()
                progressJob?.cancel()
            } else {
                p.start()
                startProgress()
            }
            updateButton(row)
            return
        }
        stopPlayback()
        val file = row.file
        if (file != null && file.isFile) startPlayer(row, file) else download(row)
    }

    private fun download(row: AudioRow) {
        if (row.downloadJob?.isActive == true) return
        val dir = File(cacheDir, AUDIO_CACHE_DIR).apply { mkdirs() }
        // Recordings run to ~115 MB an hour: keep only this session's files.
        val prefix = "${safeName(sessionId)}_"
        dir.listFiles()?.filterNot { it.name.startsWith(prefix) }?.forEach { it.delete() }
        val dest = File(dir, "$prefix${row.channel}.wav")

        row.button.isEnabled = false
        row.time.text = getString(R.string.detail_audio_downloading_plain)
        row.downloadJob = lifecycleScope.launch {
            try {
                var lastPct = -1
                client.downloadAudio(sessionId, row.channel, dest) { done, total ->
                    if (total <= 0) return@downloadAudio
                    val pct = (done * 100 / total).toInt()
                    if (pct != lastPct) {
                        lastPct = pct
                        runOnUiThread { row.time.text = getString(R.string.detail_audio_downloading, pct) }
                    }
                }
                row.file = dest
                row.time.text = ""
                if (lifecycle.currentState.isAtLeast(Lifecycle.State.STARTED) && player == null) startPlayer(row, dest)
            } catch (e: CancellationException) {
                row.time.text = ""
                throw e
            } catch (e: LiveHttpException) {
                row.time.text = if (e.status == 404) getString(R.string.detail_audio_missing)
                else getString(R.string.detail_audio_failed, getString(R.string.livesess_http, e.status))
            } catch (e: Exception) {
                row.time.text = getString(R.string.detail_audio_failed, e.message ?: e.javaClass.simpleName)
            } finally {
                row.button.isEnabled = true
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
            row.time.text = getString(R.string.detail_audio_failed, e.message ?: e.javaClass.simpleName)
            return
        }
        mp.setOnCompletionListener { stopPlayback() }
        player = mp
        playingRow = row
        row.durationMs = mp.duration.coerceAtLeast(0)
        row.seek.max = row.durationMs
        row.seek.isEnabled = true
        mp.start()
        startProgress()
        updateButton(row)
    }

    private fun startProgress() {
        progressJob?.cancel()
        progressJob = lifecycleScope.launch {
            while (isActive) {
                val p = player ?: break
                val row = playingRow ?: break
                val pos = runCatching { p.currentPosition }.getOrDefault(0)
                row.seek.progress = pos
                row.time.text = timeLabel(pos, row.durationMs)
                delay(500)
            }
        }
    }

    /** Releases the player and resets its row; safe to call when nothing is playing. */
    private fun stopPlayback() {
        progressJob?.cancel()
        progressJob = null
        player?.let { runCatching { it.stop() }; it.release() }
        player = null
        val row = playingRow ?: return
        playingRow = null
        row.seek.progress = 0
        row.seek.isEnabled = false
        row.time.text = if (row.durationMs > 0) timeLabel(0, row.durationMs) else ""
        updateButton(row)
    }

    private fun updateButton(row: AudioRow) {
        val playing = playingRow === row && player?.isPlaying == true
        row.button.text = getString(if (playing) R.string.detail_pause else R.string.detail_play)
    }

    private fun timeLabel(posMs: Int, durationMs: Int) =
        "${LocalMarkdown.clock(posMs.toLong())} / ${LocalMarkdown.clock(durationMs.toLong())}"

    // endregion

    private class SegmentAdapter : ListAdapter<RemoteSegment, SegmentAdapter.Holder>(DIFF) {
        class Holder(view: View) : RecyclerView.ViewHolder(view) {
            val time: TextView = view.findViewById(R.id.tv_seg_time)
            val speaker: TextView = view.findViewById(R.id.tv_seg_speaker)
            val text: TextView = view.findViewById(R.id.tv_seg_text)
        }

        override fun onCreateViewHolder(parent: ViewGroup, viewType: Int) =
            Holder(LayoutInflater.from(parent.context).inflate(R.layout.item_remote_segment, parent, false))

        override fun onBindViewHolder(holder: Holder, position: Int) {
            val seg = getItem(position)
            val speaker = speakerFor(seg.speaker)
            holder.time.text = LocalMarkdown.clock(seg.startMs)
            holder.speaker.bindSpeaker(speaker)
            holder.itemView.setBackgroundResource(if (speaker == Speaker.ME) R.drawable.livesess_utt_me else R.drawable.livesess_utt_them)
            holder.text.text = seg.text
        }

        companion object {
            val DIFF = object : DiffUtil.ItemCallback<RemoteSegment>() {
                override fun areItemsTheSame(a: RemoteSegment, b: RemoteSegment) = a.id == b.id
                override fun areContentsTheSame(a: RemoteSegment, b: RemoteSegment) = a == b
            }
        }
    }

    companion object {
        const val EXTRA_ID = "com.lordmuffin.jarvisvoice.live.SESSION_ID"
        const val EXTRA_TITLE = "com.lordmuffin.jarvisvoice.live.SESSION_TITLE"
        private const val STATE_TAB = "tab"
        private const val AUDIO_CACHE_DIR = "live_audio"

        fun intent(context: Context, sessionId: String, title: String?): Intent =
            Intent(context, SessionDetailActivity::class.java)
                .putExtra(EXTRA_ID, sessionId)
                .putExtra(EXTRA_TITLE, title)

        private fun safeName(id: String) = id.replace(Regex("[^A-Za-z0-9_-]"), "_")
    }
}
