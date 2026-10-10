package com.lordmuffin.jarvisvoice.live

import android.Manifest
import android.content.pm.PackageManager
import android.graphics.Typeface
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
import android.widget.Toast
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
import com.lordmuffin.jarvisvoice.R
import com.lordmuffin.jarvisvoice.live.protocol.SessionMode
import java.text.DateFormat
import java.util.Date
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/** The Live tab: start/stop a session, watch the transcript and copilot, manage pending uploads. */
class LiveActivity : AppCompatActivity() {

    private lateinit var settings: LiveSettings
    private lateinit var repo: SessionRepository

    private lateinit var tvStatus: TextView
    private lateinit var tvMessage: TextView
    private lateinit var setupGroup: View
    private lateinit var recordingGroup: View
    private lateinit var rgMode: RadioGroup
    private lateinit var etTitle: EditText
    private lateinit var swLocalOnly: SwitchCompat
    private lateinit var btnStart: Button
    private lateinit var tvPendingHeader: TextView
    private lateinit var pendingList: LinearLayout
    private lateinit var tvTimer: TextView
    private lateinit var pbLevel: ProgressBar
    private lateinit var tvDetails: TextView
    private lateinit var tvCopilotHeader: TextView
    private lateinit var svCopilot: ScrollView
    private lateinit var tvCopilot: TextView
    private lateinit var tvTranscriptEmpty: TextView
    private lateinit var rvTranscript: RecyclerView
    private val adapter = LineAdapter()

    private var copilotExpanded = true
    private var lastPending: List<SessionMeta> = emptyList()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_live)
        supportActionBar?.title = getString(R.string.live_title)
        BottomNav.wire(this, BottomNav.Tab.LIVE)
        settings = LiveSettings(this)
        repo = SessionRepository(settings.sessionsRoot)

        tvStatus = findViewById(R.id.tv_live_status)
        tvMessage = findViewById(R.id.tv_live_message)
        setupGroup = findViewById(R.id.group_live_setup)
        recordingGroup = findViewById(R.id.group_live_recording)
        rgMode = findViewById(R.id.rg_live_mode)
        etTitle = findViewById(R.id.et_live_title)
        swLocalOnly = findViewById(R.id.sw_live_local_only)
        btnStart = findViewById(R.id.btn_live_start)
        tvPendingHeader = findViewById(R.id.tv_live_pending_header)
        pendingList = findViewById(R.id.list_live_pending)
        tvTimer = findViewById(R.id.tv_live_timer)
        pbLevel = findViewById(R.id.pb_live_level)
        tvDetails = findViewById(R.id.tv_live_details)
        tvCopilotHeader = findViewById(R.id.tv_live_copilot_header)
        svCopilot = findViewById(R.id.sv_live_copilot)
        tvCopilot = findViewById(R.id.tv_live_copilot)
        tvTranscriptEmpty = findViewById(R.id.tv_live_transcript_empty)
        rvTranscript = findViewById(R.id.rv_live_transcript)

        rvTranscript.layoutManager = LinearLayoutManager(this).apply { stackFromEnd = true }
        rvTranscript.adapter = adapter

        btnStart.setOnClickListener { onStartTapped() }
        findViewById<Button>(R.id.btn_live_stop).setOnClickListener { LiveSessionService.stop(this) }
        findViewById<Button>(R.id.btn_live_marker).setOnClickListener {
            LiveSessionService.marker(this)
            Toast.makeText(this, R.string.live_marker_added, Toast.LENGTH_SHORT).show()
        }
        tvCopilotHeader.setOnClickListener {
            copilotExpanded = !copilotExpanded
            render(LiveSessionService.ui.value)
        }
        tvMessage.setOnClickListener { LiveSessionService.clearMessage() }

        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                launch { LiveSessionService.ui.collect { render(it) } }
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
        if (!settings.isConfigured) swLocalOnly.isChecked = true
    }

    private fun onStartTapped() {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), REQ_MIC)
            return
        }
        if (!swLocalOnly.isChecked && !settings.isConfigured) {
            Toast.makeText(this, R.string.live_not_configured, Toast.LENGTH_LONG).show()
            return
        }
        val mode = if (rgMode.checkedRadioButtonId == R.id.rb_live_meeting) SessionMode.MEETING else SessionMode.SOLO
        LiveSessionService.start(this, mode, etTitle.text.toString().trim(), swLocalOnly.isChecked)
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode != REQ_MIC) return
        if (grantResults.firstOrNull() == PackageManager.PERMISSION_GRANTED) onStartTapped()
        else Toast.makeText(this, R.string.live_mic_permission, Toast.LENGTH_LONG).show()
    }

    // region Rendering

    private fun render(state: LiveUiState) {
        val idle = state.phase == LiveUiState.Phase.IDLE
        setupGroup.visibility = if (idle) View.VISIBLE else View.GONE
        recordingGroup.visibility = if (idle) View.GONE else View.VISIBLE
        btnStart.isEnabled = idle

        tvStatus.text = when (state.phase) {
            LiveUiState.Phase.IDLE -> if (settings.isConfigured) "Server: ${settings.serverUrl}"
                else getString(R.string.live_not_configured)
            LiveUiState.Phase.STARTING -> "Starting…"
            LiveUiState.Phase.RECORDING -> connectionLabel(state)
            LiveUiState.Phase.STOPPING -> "Finishing — sending the rest of the audio…"
        }
        tvMessage.text = state.message
        tvMessage.visibility = if (state.message.isNullOrBlank()) View.GONE else View.VISIBLE

        pbLevel.progress = (state.level * 100).toInt()
        tvDetails.text = buildList<String?> {
            add(if (state.mode == SessionMode.MEETING) "Meeting" else "Solo")
            add(
                when (state.localStt) {
                    LiveUiState.LocalStt.READY -> "on-device drafts on"
                    LiveUiState.LocalStt.LOADING -> "loading on-device model…"
                    LiveUiState.LocalStt.UNAVAILABLE -> "no on-device model (Models tab)"
                    LiveUiState.LocalStt.OFF -> null
                }
            )
            if (state.markers > 0) add("${state.markers} marker${if (state.markers == 1) "" else "s"}")
        }.filterNotNull().joinToString(" · ")

        renderCopilot(state.session)
        val lines = state.session.lines
        adapter.submitList(lines) {
            if (lines.isNotEmpty()) rvTranscript.scrollToPosition(lines.lastIndex)
        }
        tvTranscriptEmpty.visibility = if (lines.isEmpty() && !idle) View.VISIBLE else View.GONE

        state.session.finalNote?.let { note ->
            if (idle && state.message == null) tvStatus.text = "Saved to ${note.path}"
        }
    }

    private fun connectionLabel(state: LiveUiState): String {
        if (state.localOnly) return "● Recording locally"
        val conn = when (val c = state.connection) {
            ConnState.Connected -> "● Live"
            ConnState.Connecting, ConnState.Idle -> "Connecting…"
            is ConnState.Reconnecting -> "Offline — retrying (${c.attempt})"
            ConnState.Ended -> "Ended"
            is ConnState.Failed -> "Failed: ${c.reason}"
        }
        val parts = mutableListOf(conn)
        state.session.sttTier?.let { parts += "STT $it" }
        if (state.session.lagMs > 0) parts += "lag ${state.session.lagMs} ms"
        if (state.unackedMs > 1_000) parts += "%.1f s queued".format(state.unackedMs / 1000.0)
        return parts.joinToString(" · ")
    }

    private fun renderCopilot(snapshot: SessionSnapshot) {
        val copilot = snapshot.copilot
        val text = copilot?.let {
            buildString {
                fun section(title: String, items: List<String>) {
                    if (items.isEmpty()) return
                    if (isNotEmpty()) append('\n')
                    append(title).append('\n')
                    items.forEach { append("• ").append(it).append('\n') }
                }
                section("Notes", it.notes.map { n -> n.text })
                section("Actions", it.actions.map { a ->
                    listOfNotNull(a.text, a.owner?.let { o -> "($o)" }, a.due?.let { d -> "due $d" }).joinToString(" ")
                })
                section("Decisions", it.decisions.map { d -> d.text })
                section("Suggestions", it.suggestions.map { s -> s.text })
                section("Related", it.related.map { r -> r.title })
            }.trimEnd()
        }
        val has = !text.isNullOrEmpty()
        tvCopilotHeader.visibility = if (has) View.VISIBLE else View.GONE
        tvCopilotHeader.text = "${getString(R.string.live_copilot)} ${if (copilotExpanded) "▼" else "▶"}"
        svCopilot.visibility = if (has && copilotExpanded) View.VISIBLE else View.GONE
        tvCopilot.text = text
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
                meta.state == SessionMeta.State.FAILED -> "Failed: ${meta.error ?: "server rejected it"}"
                meta.serverId == null -> "Recorded locally · $started"
                meta.state == SessionMeta.State.RECORDING -> "Interrupted · resuming upload"
                else -> "Uploading in the background"
            }
            val upload = row.findViewById<Button>(R.id.btn_pending_upload)
            upload.visibility = if (meta.state == SessionMeta.State.FAILED && meta.serverId != null) View.GONE else View.VISIBLE
            upload.setOnClickListener {
                if (!settings.isConfigured) {
                    Toast.makeText(this, R.string.live_not_configured, Toast.LENGTH_LONG).show()
                    return@setOnClickListener
                }
                if (meta.state == SessionMeta.State.FAILED) repo.write(meta.copy(state = SessionMeta.State.PENDING, error = null))
                LiveUploadWorker.enqueue(this, meta.id)
                Toast.makeText(this, R.string.live_upload_queued, Toast.LENGTH_SHORT).show()
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
            val text: TextView = view.findViewById(R.id.tv_line_text)
        }

        override fun onCreateViewHolder(parent: ViewGroup, viewType: Int) =
            Holder(LayoutInflater.from(parent.context).inflate(R.layout.item_live_line, parent, false))

        override fun onBindViewHolder(holder: Holder, position: Int) {
            val line = getItem(position)
            val ctx = holder.itemView.context
            holder.time.text = LocalMarkdown.clock(line.startMs)
            holder.text.text = line.text
            holder.text.setTypeface(null, if (line.isDraft) Typeface.ITALIC else Typeface.NORMAL)
            holder.text.setTextColor(ctx.getColor(if (line.isDraft) R.color.jv_text2 else R.color.jv_text))
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
    }
}
