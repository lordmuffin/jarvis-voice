package com.lordmuffin.jarvisvoice.live

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Typeface
import android.text.format.DateUtils
import android.os.Bundle
import android.os.SystemClock
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.RadioGroup
import android.widget.ScrollView
import android.widget.TextView
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.appcompat.widget.SwitchCompat
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import androidx.recyclerview.widget.DiffUtil
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.ListAdapter
import androidx.recyclerview.widget.RecyclerView
import androidx.work.WorkManager
import com.lordmuffin.jarvisvoice.BottomNav
import com.lordmuffin.jarvisvoice.DictationHistoryManager
import com.lordmuffin.jarvisvoice.R
import com.lordmuffin.jarvisvoice.SettingsActivity
import com.lordmuffin.jarvisvoice.VoiceOverlayService
import com.lordmuffin.jarvisvoice.live.protocol.SessionMode
import com.lordmuffin.jarvisvoice.live.protocol.Speaker
import com.lordmuffin.jarvisvoice.ui.InstToast
import com.lordmuffin.jarvisvoice.ui.bindConnection
import com.lordmuffin.jarvisvoice.ui.bindSpeaker
import com.lordmuffin.jarvisvoice.ui.setTextWithChannel
import java.text.DateFormat
import java.util.Date
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.launch

/** The Record tab: start/stop a session, watch the transcript and copilot, manage pending uploads. */
class LiveActivity : AppCompatActivity() {

    private lateinit var settings: LiveSettings
    private lateinit var repo: SessionRepository
    private lateinit var networkMonitor: NetworkPlaceMonitor

    private lateinit var tvStatus: TextView
    private lateinit var tvMessage: TextView
    private lateinit var chipConnection: TextView
    private lateinit var chipCopilot: TextView
    private lateinit var tvDictationStatus: TextView
    private lateinit var tvDictationLast: TextView
    private lateinit var setupGroup: View
    private lateinit var recordingGroup: View
    private lateinit var rgMode: RadioGroup
    private lateinit var etTitle: EditText
    private lateinit var swLocalOnly: SwitchCompat
    private lateinit var tvNetwork: TextView
    private lateinit var btnStart: Button
    private lateinit var btnHistory: Button
    private lateinit var tvPendingHeader: TextView
    private lateinit var pendingList: LinearLayout
    private lateinit var tvTimer: TextView
    private lateinit var pbLevel: ProgressBar
    private lateinit var tvDetails: TextView
    private lateinit var tvCopilotHeader: TextView
    private lateinit var svCopilot: ScrollView
    private lateinit var tvCopilot: TextView
    private lateinit var groupTranscriptEmpty: View
    private lateinit var rvTranscript: RecyclerView
    private val adapter = LineAdapter()

    private var copilotExpanded = true
    private val wide by lazy { resources.getBoolean(R.bool.is_wide) }
    private var lastPending: List<SessionMeta> = emptyList()
    /** Set while [renderAwayMode] moves the switch, so it doesn't count as the user's choice. */
    private var settingSwitch = false
    private var checkingNetwork = false
    /** The release channel `GET /healthz` reported, and for which server URL. */
    private var serverChannel: Pair<String, ServerChannel>? = null
    private var channelJob: Job? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_live)
        supportActionBar?.title = getString(R.string.live_title)
        BottomNav.wire(this, BottomNav.Tab.RECORD)
        settings = LiveSettings(this)
        repo = SessionRepository(settings.sessionsRoot)
        networkMonitor = NetworkPlaceMonitor(this)

        tvStatus = findViewById(R.id.tv_live_status)
        tvMessage = findViewById(R.id.tv_live_message)
        chipConnection = findViewById(R.id.chip_live_connection)
        chipCopilot = findViewById(R.id.chip_live_copilot)
        tvDictationStatus = findViewById(R.id.tv_dictation_status)
        tvDictationLast = findViewById(R.id.tv_dictation_last)
        findViewById<View>(R.id.row_dictation).setOnClickListener {
            startActivity(Intent(this, SettingsActivity::class.java))
        }
        setupGroup = findViewById(R.id.group_live_setup)
        recordingGroup = findViewById(R.id.group_live_recording)
        rgMode = findViewById(R.id.rg_live_mode)
        etTitle = findViewById(R.id.et_live_title)
        swLocalOnly = findViewById(R.id.sw_live_local_only)
        tvNetwork = findViewById(R.id.tv_live_network)
        btnStart = findViewById(R.id.btn_live_start)
        btnHistory = findViewById(R.id.btn_live_history)
        tvPendingHeader = findViewById(R.id.tv_live_pending_header)
        pendingList = findViewById(R.id.list_live_pending)
        tvTimer = findViewById(R.id.tv_live_timer)
        pbLevel = findViewById(R.id.pb_live_level)
        tvDetails = findViewById(R.id.tv_live_details)
        tvCopilotHeader = findViewById(R.id.tv_live_copilot_header)
        svCopilot = findViewById(R.id.sv_live_copilot)
        tvCopilot = findViewById(R.id.tv_live_copilot)
        groupTranscriptEmpty = findViewById(R.id.group_live_transcript_empty)
        rvTranscript = findViewById(R.id.rv_live_transcript)

        rvTranscript.layoutManager = LinearLayoutManager(this).apply { stackFromEnd = true }
        rvTranscript.adapter = adapter

        btnStart.setOnClickListener { onStartTapped() }
        swLocalOnly.setOnCheckedChangeListener { _, checked ->
            if (settingSwitch || !settings.isConfigured) return@setOnCheckedChangeListener
            settings.awayMode = settings.awayMode.withManual(checked)
            renderAwayMode()
        }
        btnHistory.setOnClickListener { startActivity(Intent(this, SessionsActivity::class.java)) }
        findViewById<Button>(R.id.btn_live_stop).setOnClickListener { LiveSessionService.stop(this) }
        findViewById<Button>(R.id.btn_live_marker).setOnClickListener {
            LiveSessionService.marker(this)
            InstToast.show(this, R.string.live_marker_added)
        }
        if (!wide) tvCopilotHeader.setOnClickListener {
            copilotExpanded = !copilotExpanded
            render(LiveSessionService.ui.value)
        }
        tvMessage.setOnClickListener { LiveSessionService.clearMessage() }

        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                launch { LiveSessionService.ui.collect { render(it) } }
                launch {
                    // A burst of network callbacks settles into one check; a newer change
                    // cancels a check still waiting on the server.
                    networkMonitor.changes().collectLatest {
                        delay(NETWORK_SETTLE_MS)
                        checkNetwork()
                    }
                }
                launch {
                    // Timer and pending list (upload workers update metadata in the background).
                    while (true) {
                        val state = LiveSessionService.ui.value
                        if (state.phase == LiveUiState.Phase.RECORDING) {
                            tvTimer.text = LocalMarkdown.clock(SystemClock.elapsedRealtime() - state.startedElapsed)
                        }
                        refreshPending(state)
                        delay(1_000)
                    }
                }
            }
        }
    }

    override fun onResume() {
        super.onResume()
        // Finish online sessions a crash or network loss left behind.
        LiveUploadWorker.enqueueInterrupted(this, LiveSessionService.ui.value.sessionId)
        renderAwayMode()
        refreshServerChannel()
        refreshDictation()
    }

    /** The dictation row: whether the overlay is running and the last dictation's numbers. */
    private fun refreshDictation() {
        val running = VoiceOverlayService.instance != null
        tvDictationStatus.setText(if (running) R.string.home_dictation_running else R.string.home_dictation_stopped)
        tvDictationStatus.setTextColor(getColor(if (running) R.color.inst_accent else R.color.inst_text_2))
        val last = runCatching {
            val history = DictationHistoryManager(this)
            try { history.getLastSession() } finally { history.close() }
        }.getOrNull()
        tvDictationLast.text = if (last == null) {
            getString(R.string.home_dictation_none)
        } else {
            getString(
                R.string.home_dictation_last, last.wordCount, "%.0f".format(last.wpm),
                if (last.transcript != last.rawTranscript) getString(R.string.home_dictation_enhanced) else "",
                DateUtils.getRelativeTimeSpanString(last.timestamp, System.currentTimeMillis(), DateUtils.MINUTE_IN_MILLIS),
            )
        }
    }

    /** Asks the server which release channel it is, for the tag beside its address. */
    private fun refreshServerChannel() {
        channelJob?.cancel()
        if (!settings.isConfigured) return
        val url = settings.serverUrl
        val client = runCatching { LiveApiClient(url, "") }.getOrNull() ?: return
        channelJob = lifecycleScope.launch {
            serverChannel = client.channel()?.let { url to it }
            render(LiveSessionService.ui.value)
        }
    }

    /** Away from home and off NetBird → Local only, unless the user chose otherwise. */
    private suspend fun checkNetwork() {
        if (!settings.isConfigured || !settings.awayMode.enabled) return renderAwayMode()
        checkingNetwork = true
        renderAwayMode()
        try {
            val place = networkMonitor.check(settings.apiClient())
            settings.awayMode = settings.awayMode.withPlace(place)
        } finally {
            checkingNetwork = false
            renderAwayMode()
        }
    }

    private fun onStartTapped() {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), REQ_MIC)
            return
        }
        if (!swLocalOnly.isChecked && !settings.isConfigured) {
            InstToast.show(this, R.string.live_not_configured)
            return
        }
        val mode = if (rgMode.checkedRadioButtonId == R.id.rb_live_meeting) SessionMode.MEETING else SessionMode.SOLO
        LiveSessionService.start(this, mode, etTitle.text.toString().trim(), swLocalOnly.isChecked)
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode != REQ_MIC) return
        if (grantResults.firstOrNull() == PackageManager.PERMISSION_GRANTED) onStartTapped()
        else InstToast.show(this, R.string.live_mic_permission)
    }

    // region Rendering

    private fun renderAwayMode() {
        val away = settings.awayMode
        settingSwitch = true
        swLocalOnly.isChecked = !settings.isConfigured || away.localOnly
        settingSwitch = false
        val label = awayLabel(away)
        tvNetwork.text = label
        tvNetwork.visibility = if (label == null) View.GONE else View.VISIBLE
    }

    private fun awayLabel(away: AutoLocalOnly): String? {
        if (!settings.isConfigured || !away.enabled) return null
        val where = when (away.place) {
            NetworkPlace.HOME -> R.string.live_away_home
            NetworkPlace.NETBIRD -> R.string.live_away_netbird
            NetworkPlace.AWAY -> R.string.live_away_away
            null -> return getString(R.string.live_away_checking)
        }
        val what = when {
            away.manual != null && away.automatic == true -> R.string.live_away_manual_until_back
            away.manual != null -> R.string.live_away_manual_until_leave
            away.localOnly -> R.string.live_away_local
            else -> R.string.live_away_streaming
        }
        return listOfNotNull(
            getString(where), getString(what), if (checkingNetwork) getString(R.string.live_away_rechecking) else null,
        ).joinToString(" · ")
    }

    private fun render(state: LiveUiState) {
        val idle = state.phase == LiveUiState.Phase.IDLE
        setupGroup.visibility = if (idle) View.VISIBLE else View.GONE
        recordingGroup.visibility = if (idle) View.GONE else View.VISIBLE
        btnStart.isEnabled = idle

        when (state.phase) {
            LiveUiState.Phase.IDLE -> if (settings.isConfigured) {
                val url = settings.serverUrl
                tvStatus.setTextWithChannel(getString(R.string.livesess_server, url), serverChannel?.takeIf { it.first == url }?.second)
            } else {
                tvStatus.text = getString(R.string.live_not_configured)
            }
            LiveUiState.Phase.STARTING -> tvStatus.text = getString(R.string.livesess_starting)
            LiveUiState.Phase.RECORDING -> tvStatus.text = connectionDetail(state)
            LiveUiState.Phase.STOPPING -> tvStatus.text = getString(R.string.livesess_finishing)
        }
        val chip = if (idle) ConnectionChip(ConnectionKind.IDLE)
        else ConnectionChip.of(state.connection, state.localOnly, state.session.lagMs, state.session.llmOk)
        chipConnection.bindConnection(chip)
        chipCopilot.visibility = if (chip.copilotOffline) View.VISIBLE else View.GONE
        tvMessage.text = state.message
        tvMessage.visibility = if (state.message.isNullOrBlank()) View.GONE else View.VISIBLE

        pbLevel.progress = (state.level * 100).toInt()
        tvDetails.text = buildList<String?> {
            add(getString(if (state.mode == SessionMode.MEETING) R.string.live_mode_meeting else R.string.live_mode_solo))
            add(
                when (state.localStt) {
                    LiveUiState.LocalStt.READY -> getString(R.string.livesess_drafts_on)
                    LiveUiState.LocalStt.LOADING -> getString(R.string.livesess_model_loading)
                    LiveUiState.LocalStt.UNAVAILABLE -> getString(R.string.livesess_model_missing)
                    LiveUiState.LocalStt.OFF -> null
                }
            )
            if (state.markers > 0) add(if (state.markers == 1) getString(R.string.livesess_marker_one) else getString(R.string.livesess_markers, state.markers))
        }.filterNotNull().joinToString(" · ")

        val streamMs = if (idle) null else SystemClock.elapsedRealtime() - state.startedElapsed
        renderCopilot(state.session, streamMs)
        val lines = state.session.lines
        adapter.submitList(lines) {
            if (lines.isNotEmpty()) rvTranscript.scrollToPosition(lines.lastIndex)
        }
        groupTranscriptEmpty.visibility = if (lines.isEmpty() && !idle) View.VISIBLE else View.GONE

        state.session.finalNote?.let { note ->
            if (idle && state.message == null) tvStatus.text = getString(R.string.livesess_saved_to, note.path)
        }
    }

    /** What sits beside the connection chip while recording: tier, lag, queued audio, reconnect attempt. */
    private fun connectionDetail(state: LiveUiState): String {
        if (state.localOnly) return getString(R.string.livesess_rec_local)
        val parts = mutableListOf<String>()
        when (val c = state.connection) {
            is ConnState.Reconnecting -> parts += getString(R.string.conn_attempt, c.attempt)
            is ConnState.Failed -> parts += getString(R.string.conn_failed_reason, c.reason)
            else -> {}
        }
        state.session.sttTier?.let { parts += getString(R.string.livesess_stt_tier, it) }
        if (state.session.lagMs > 0) parts += getString(R.string.livesess_lag, state.session.lagMs)
        if (state.unackedMs > 1_000) parts += getString(R.string.livesess_queued, state.unackedMs / 1000.0)
        return parts.joinToString(" · ")
    }

    private fun renderCopilot(snapshot: SessionSnapshot, streamMs: Long?) {
        val sections = CopilotSections.build(snapshot.copilot, snapshot.finalNote, streamMs)
        val text = if (sections.isEmpty()) null else buildString {
            for ((section, entries) in sections) {
                if (isNotEmpty()) append('\n')
                append(sectionTitle(section)).append('\n')
                for (e in entries) {
                    append("• ")
                    e.kind?.let { append(kindLabel(it)).append(": ") }
                    append(listOfNotNull(e.text, e.owner?.let { o -> "($o)" }, e.due?.let { d -> getString(R.string.detail_due, d) }).joinToString(" "))
                    append('\n')
                }
            }
        }?.trimEnd()
        val has = !text.isNullOrEmpty()
        if (wide) {
            // Unfolded: the copilot has its own pane, always open, so there is nothing to collapse.
            tvCopilotHeader.visibility = View.VISIBLE
            svCopilot.visibility = View.VISIBLE
            tvCopilot.text = text
            return
        }
        tvCopilotHeader.visibility = if (has) View.VISIBLE else View.GONE
        tvCopilotHeader.text = "${getString(R.string.live_copilot)} ${getString(if (copilotExpanded) R.string.livesess_copilot_expanded else R.string.livesess_copilot_collapsed)}"
        svCopilot.visibility = if (has && copilotExpanded) View.VISIBLE else View.GONE
        tvCopilot.text = text
    }

    private fun sectionTitle(section: CopilotSection) = getString(
        when (section) {
            CopilotSection.SUGGESTIONS -> R.string.copilot_section_suggestions
            CopilotSection.ACTIONS -> R.string.copilot_section_actions
            CopilotSection.DECISIONS -> R.string.copilot_section_decisions
            CopilotSection.NOTES -> R.string.copilot_section_notes
            CopilotSection.RELATED -> R.string.copilot_section_related
            CopilotSection.FINAL_NOTE -> R.string.copilot_section_final_note
        }
    )

    private fun kindLabel(kind: String) = when (kind) {
        "question" -> getString(R.string.copilot_kind_question)
        "gap" -> getString(R.string.copilot_kind_gap)
        "counterpoint" -> getString(R.string.copilot_kind_counterpoint)
        "fact_check" -> getString(R.string.copilot_kind_fact_check)
        else -> kind
    }

    private fun refreshPending(state: LiveUiState) {
        if (state.phase != LiveUiState.Phase.IDLE) return
        val pending = repo.pending(state.sessionId.takeIf { state.isActive })
        if (pending == lastPending) return
        lastPending = pending
        tvPendingHeader.visibility = if (pending.isEmpty()) View.GONE else View.VISIBLE
        pendingList.removeAllViews()
        val inflater = LayoutInflater.from(this)
        for (meta in pending) {
            val row = inflater.inflate(R.layout.item_live_pending, pendingList, false)
            val started = DateFormat.getDateTimeInstance(DateFormat.MEDIUM, DateFormat.SHORT).format(Date(meta.startedAt))
            row.findViewById<TextView>(R.id.tv_pending_title).text = meta.title ?: started
            row.findViewById<TextView>(R.id.tv_pending_state).text = when {
                meta.state == SessionMeta.State.FAILED -> getString(R.string.livesess_pending_failed, meta.error ?: getString(R.string.livesess_pending_rejected))
                meta.serverId == null -> getString(R.string.livesess_pending_local, started)
                meta.state == SessionMeta.State.RECORDING -> getString(R.string.livesess_pending_interrupted)
                else -> getString(R.string.livesess_pending_uploading)
            }
            val upload = row.findViewById<Button>(R.id.btn_pending_upload)
            upload.visibility = if (meta.state == SessionMeta.State.FAILED && meta.serverId != null) View.GONE else View.VISIBLE
            upload.setOnClickListener {
                if (!settings.isConfigured) {
                    InstToast.show(this, R.string.live_not_configured)
                    return@setOnClickListener
                }
                if (meta.state == SessionMeta.State.FAILED) repo.write(meta.copy(state = SessionMeta.State.PENDING, error = null))
                LiveUploadWorker.enqueue(this, meta.id)
                InstToast.show(this, R.string.live_upload_queued)
            }
            row.findViewById<Button>(R.id.btn_pending_delete).setOnClickListener { confirmDelete(meta) }
            pendingList.addView(row)
        }
    }

    private fun confirmDelete(meta: SessionMeta) {
        AlertDialog.Builder(this)
            .setMessage(R.string.live_delete_confirm)
            .setPositiveButton(R.string.live_delete) { _, _ ->
                WorkManager.getInstance(this).cancelUniqueWork(LiveUploadWorker.workName(meta.id))
                repo.delete(meta.id)
                lastPending = emptyList()
                refreshPending(LiveSessionService.ui.value)
            }
            .setNegativeButton(android.R.string.cancel, null)
            .show()
    }

    // endregion

    private class LineAdapter : ListAdapter<TranscriptLine, LineAdapter.Holder>(DIFF) {
        class Holder(view: View) : RecyclerView.ViewHolder(view) {
            val time: TextView = view.findViewById(R.id.tv_line_time)
            val speaker: TextView = view.findViewById(R.id.tv_line_speaker)
            val text: TextView = view.findViewById(R.id.tv_line_text)
        }

        override fun onCreateViewHolder(parent: ViewGroup, viewType: Int) =
            Holder(LayoutInflater.from(parent.context).inflate(R.layout.item_live_line, parent, false))

        override fun onBindViewHolder(holder: Holder, position: Int) {
            val line = getItem(position)
            val ctx = holder.itemView.context
            holder.time.text = LocalMarkdown.clock(line.startMs)
            holder.speaker.bindSpeaker(line.speaker)
            holder.text.text = line.text
            holder.text.setTypeface(null, if (line.isDraft) Typeface.ITALIC else Typeface.NORMAL)
            holder.itemView.setBackgroundResource(if (line.speaker == Speaker.ME) R.drawable.livesess_utt_me else R.drawable.livesess_utt_them)
            holder.text.setTextColor(ctx.getColor(if (line.isDraft) R.color.inst_text_2 else R.color.inst_text))
        }

        companion object {
            val DIFF = object : DiffUtil.ItemCallback<TranscriptLine>() {
                override fun areItemsTheSame(a: TranscriptLine, b: TranscriptLine) = a.id == b.id
                override fun areContentsTheSame(a: TranscriptLine, b: TranscriptLine) = a == b
            }
        }
    }

    private companion object {
        const val REQ_MIC = 7101
        const val NETWORK_SETTLE_MS = 750L
    }
}
