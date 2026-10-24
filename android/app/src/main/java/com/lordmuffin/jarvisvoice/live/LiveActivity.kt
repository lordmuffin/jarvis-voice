package com.lordmuffin.jarvisvoice.live

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.os.SystemClock
import android.text.format.DateUtils
import androidx.activity.compose.setContent
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import androidx.work.WorkManager
import com.lordmuffin.jarvisvoice.BottomNav
import com.lordmuffin.jarvisvoice.DictationHistoryManager
import com.lordmuffin.jarvisvoice.R
import com.lordmuffin.jarvisvoice.SettingsActivity
import com.lordmuffin.jarvisvoice.VoiceOverlayService
import com.lordmuffin.jarvisvoice.live.protocol.SessionMode
import com.lordmuffin.jarvisvoice.ui.InstToast
import com.lordmuffin.jarvisvoice.ui.compose.CopilotBlock
import com.lordmuffin.jarvisvoice.ui.compose.DictationUi
import com.lordmuffin.jarvisvoice.ui.compose.FeedLine
import com.lordmuffin.jarvisvoice.ui.compose.JarvisTheme
import com.lordmuffin.jarvisvoice.ui.compose.LevelHistory
import com.lordmuffin.jarvisvoice.ui.compose.PendingUi
import com.lordmuffin.jarvisvoice.ui.compose.RecentRow
import com.lordmuffin.jarvisvoice.ui.compose.RecentUi
import com.lordmuffin.jarvisvoice.ui.compose.RecordActions
import com.lordmuffin.jarvisvoice.ui.compose.RecordPhase
import com.lordmuffin.jarvisvoice.ui.compose.RecordScreen
import com.lordmuffin.jarvisvoice.ui.compose.RecordUi
import com.lordmuffin.jarvisvoice.ui.compose.Tone
import java.text.DateFormat
import java.util.Date
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.launch

/**
 * The Record tab: start/stop a session, watch the transcript and copilot, manage pending uploads, and
 * see recent history on the same page. The screen is Compose (`RecordScreen`); this class owns the
 * service, permission and network logic and hands the screen a [RecordUi] to draw.
 */
class LiveActivity : AppCompatActivity() {

    private lateinit var settings: LiveSettings
    private lateinit var repo: SessionRepository
    private lateinit var networkMonitor: NetworkPlaceMonitor

    private var ui by mutableStateOf(RecordUi())

    private var copilotExpanded = true
    private var lastPending: List<SessionMeta> = emptyList()
    private var checkingNetwork = false
    /** The release channel `GET /healthz` reported, and for which server URL. */
    private var serverChannel: Pair<String, ServerChannel>? = null
    private var channelJob: Job? = null
    private var recentJob: Job? = null
    private var recentClient: LiveApiClient? = null
    private var recentClientKey: Pair<String, String?>? = null
    /** The last few input levels, for the live waveform. UI-only; recording is untouched. */
    private val levels = LevelHistory(LEVEL_SAMPLES)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        settings = LiveSettings(this)
        repo = SessionRepository(settings.sessionsRoot)
        networkMonitor = NetworkPlaceMonitor(this)

        setContent {
            JarvisTheme {
                RecordScreen(
                    ui,
                    RecordActions(
                        onStart = ::onStartTapped,
                        onStop = { LiveSessionService.stop(this) },
                        onMarker = {
                            LiveSessionService.marker(this)
                            InstToast.show(this, R.string.live_marker_added)
                        },
                        onHistory = { startActivity(Intent(this, SessionsActivity::class.java)) },
                        onOpenSession = { id ->
                            // Unfolded the history opens with the session beside the list; folded it opens on its own.
                            if (resources.getBoolean(R.bool.is_wide)) startActivity(SessionsActivity.intent(this, id))
                            else startActivity(SessionDetailActivity.intent(this, id, (ui.recent as? RecentUi.Items)?.rows?.firstOrNull { it.id == id }?.title))
                        },
                        onModeChange = { ui = ui.copy(mode = it) },
                        onTitleChange = { ui = ui.copy(title = it) },
                        onLocalOnlyChange = ::onLocalOnlyChanged,
                        onDictation = { startActivity(Intent(this, SettingsActivity::class.java)) },
                        onDismissMessage = { LiveSessionService.clearMessage() },
                        onToggleCopilot = {
                            copilotExpanded = !copilotExpanded
                            render(LiveSessionService.ui.value)
                        },
                        onUpload = ::uploadPending,
                        onDelete = { id -> lastPending.firstOrNull { it.id == id }?.let(::confirmDelete) },
                        onRetryRecent = ::refreshRecent,
                        onOpenSettings = { startActivity(Intent(this, SettingsActivity::class.java)) },
                        onNav = { BottomNav.open(this, it) },
                    ),
                )
            }
        }

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
                            ui = ui.copy(timer = LocalMarkdown.clock(SystemClock.elapsedRealtime() - state.startedElapsed))
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
        refreshRecent()
    }

    /** The dictation row: whether the overlay is running and the last dictation's numbers. */
    private fun refreshDictation() {
        val running = VoiceOverlayService.instance != null
        val last = runCatching {
            val history = DictationHistoryManager(this)
            try { history.getLastSession() } finally { history.close() }
        }.getOrNull()
        val text = if (last == null) {
            getString(R.string.home_dictation_none)
        } else {
            getString(
                R.string.home_dictation_last, last.wordCount, "%.0f".format(last.wpm),
                if (last.transcript != last.rawTranscript) getString(R.string.home_dictation_enhanced) else "",
                DateUtils.getRelativeTimeSpanString(last.timestamp, System.currentTimeMillis(), DateUtils.MINUTE_IN_MILLIS),
            )
        }
        ui = ui.copy(dictation = DictationUi(running, text))
    }

    /** The saved server's channel once /healthz has answered for the current URL; null until then. */
    private fun currentChannel(): ServerChannel? =
        serverChannel?.takeIf { it.first == settings.serverUrl }?.second

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

    /** The last few server sessions, for the history block under the new-session controls. Read-only. */
    private fun refreshRecent() {
        recentJob?.cancel()
        if (!settings.isConfigured) {
            ui = ui.copy(recent = RecentUi.SignedOut)
            return
        }
        if (ui.recent !is RecentUi.Items) ui = ui.copy(recent = RecentUi.Loading)
        else ui = ui.copy(recent = (ui.recent as RecentUi.Items).copy(refreshing = true))
        recentJob = lifecycleScope.launch {
            try {
                val key = settings.serverUrl to settings.deviceToken
                if (key != recentClientKey) {
                    recentClient = settings.apiClient()
                    recentClientKey = key
                }
                val page = recentClient!!.listSessions(RECENT_COUNT, 0)
                ui = ui.copy(
                    recent = if (page.items.isEmpty()) RecentUi.Empty
                    else RecentUi.Items(page.items.map { SessionLabels.row(this@LiveActivity, it) }),
                )
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                ui = ui.copy(
                    recent = if (e is LiveHttpException && e.status == 401) RecentUi.Expired
                    else RecentUi.Error(SessionLabels.errorText(this@LiveActivity, e)),
                )
            }
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

    private fun onLocalOnlyChanged(checked: Boolean) {
        if (!settings.isConfigured) return
        settings.awayMode = settings.awayMode.withManual(checked)
        renderAwayMode()
    }

    private fun onStartTapped() {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), REQ_MIC)
            return
        }
        if (!ui.localOnly && !settings.isConfigured) {
            InstToast.show(this, R.string.live_not_configured)
            return
        }
        LiveSessionService.start(this, ui.mode, ui.title.trim(), ui.localOnly)
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
        ui = ui.copy(
            localOnly = !settings.isConfigured || away.localOnly,
            networkLabel = awayLabel(away),
        )
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
        var serverLine = when (state.phase) {
            LiveUiState.Phase.IDLE ->
                if (settings.isConfigured) getString(R.string.livesess_server, settings.serverUrl) else getString(R.string.live_not_configured)
            LiveUiState.Phase.STARTING -> getString(R.string.livesess_starting)
            LiveUiState.Phase.RECORDING -> connectionDetail(state)
            LiveUiState.Phase.STOPPING -> getString(R.string.livesess_finishing)
        }
        val chip = if (idle) ConnectionChip(ConnectionKind.IDLE)
        else ConnectionChip.of(state.connection, state.localOnly, state.session.lagMs, state.session.llmOk)

        if (state.phase == LiveUiState.Phase.RECORDING) levels.push(state.level) else levels.clear()

        val details = buildList<String?> {
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
        state.session.finalNote?.let { note ->
            if (idle && state.message == null) serverLine = getString(R.string.livesess_saved_to, note.path)
        }

        ui = ui.copy(
            phase = when (state.phase) {
                LiveUiState.Phase.IDLE -> RecordPhase.IDLE
                LiveUiState.Phase.STARTING -> RecordPhase.STARTING
                LiveUiState.Phase.RECORDING -> RecordPhase.RECORDING
                LiveUiState.Phase.STOPPING -> RecordPhase.STOPPING
            },
            serverLine = serverLine,
            channel = currentChannel(),
            connection = chip.kind,
            connectionDetail = if (idle) "" else connectionDetail(state),
            copilotOffline = chip.copilotOffline,
            message = state.message,
            levels = levels.snapshot(),
            details = details,
            sessionTitle = ui.title.trim(),
            transcript = state.session.lines.map {
                FeedLine(it.id, LocalMarkdown.clock(it.startMs), it.speaker, it.text, it.isDraft)
            },
            copilot = copilotBlocks(state.session, streamMs),
            copilotExpanded = copilotExpanded,
        )
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

    private fun copilotBlocks(snapshot: SessionSnapshot, streamMs: Long?): List<CopilotBlock> =
        CopilotSections.build(snapshot.copilot, snapshot.finalNote, streamMs).map { (section, entries) ->
            CopilotBlock(
                sectionTitle(section),
                entries.map { e ->
                    buildString {
                        e.kind?.let { append(kindLabel(it)).append(": ") }
                        append(
                            listOfNotNull(e.text, e.owner?.let { o -> "($o)" }, e.due?.let { d -> getString(R.string.detail_due, d) }).joinToString(" ")
                        )
                    }
                },
            )
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
        ui = ui.copy(pending = pending.map { meta ->
            val started = DateFormat.getDateTimeInstance(DateFormat.MEDIUM, DateFormat.SHORT).format(Date(meta.startedAt))
            val failed = meta.state == SessionMeta.State.FAILED
            PendingUi(
                id = meta.id,
                title = meta.title ?: started,
                state = when {
                    failed -> getString(R.string.livesess_pending_failed, meta.error ?: getString(R.string.livesess_pending_rejected))
                    meta.serverId == null -> getString(R.string.livesess_pending_local, started)
                    meta.state == SessionMeta.State.RECORDING -> getString(R.string.livesess_pending_interrupted)
                    else -> getString(R.string.livesess_pending_uploading)
                },
                tone = when {
                    failed -> Tone.Error
                    meta.serverId != null -> Tone.Warning
                    else -> Tone.Neutral
                },
                canUpload = !(failed && meta.serverId != null),
            )
        })
    }

    private fun uploadPending(id: String) {
        val meta = lastPending.firstOrNull { it.id == id } ?: return
        if (!settings.isConfigured) {
            InstToast.show(this, R.string.live_not_configured)
            return
        }
        if (meta.state == SessionMeta.State.FAILED) repo.write(meta.copy(state = SessionMeta.State.PENDING, error = null))
        LiveUploadWorker.enqueue(this, meta.id)
        InstToast.show(this, R.string.live_upload_queued)
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

    private companion object {
        const val REQ_MIC = 7101
        const val NETWORK_SETTLE_MS = 750L
        const val LEVEL_SAMPLES = 64
        const val RECENT_COUNT = 5
    }
}
