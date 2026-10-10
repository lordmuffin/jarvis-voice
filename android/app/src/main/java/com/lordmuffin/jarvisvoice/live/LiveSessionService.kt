package com.lordmuffin.jarvisvoice.live

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import android.os.SystemClock
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import com.lordmuffin.jarvisvoice.DebugLog
import com.lordmuffin.jarvisvoice.R
import com.lordmuffin.jarvisvoice.VoiceOverlayService
import com.lordmuffin.jarvisvoice.live.protocol.Channel
import com.lordmuffin.jarvisvoice.live.protocol.ClientMessage
import com.lordmuffin.jarvisvoice.live.protocol.DraftSegment
import com.lordmuffin.jarvisvoice.live.protocol.SessionMode
import com.lordmuffin.jarvisvoice.speech.SherpaOnnxSpeechEngine
import com.lordmuffin.jarvisvoice.speech.WhisperRecognizer
import java.io.IOException
import java.util.UUID
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull

/** What the Live screen renders. */
data class LiveUiState(
    val phase: Phase = Phase.IDLE,
    val sessionId: String? = null,
    val mode: SessionMode = SessionMode.SOLO,
    val localOnly: Boolean = false,
    /** `SystemClock.elapsedRealtime()` when capture started, for the timer. */
    val startedElapsed: Long = 0,
    val connection: ConnState = ConnState.Idle,
    val localStt: LocalStt = LocalStt.OFF,
    val session: SessionSnapshot = SessionSnapshot(),
    val unackedMs: Int = 0,
    val level: Float = 0f,
    val markers: Int = 0,
    /** Last user-facing problem or notice. */
    val message: String? = null,
) {
    enum class Phase { IDLE, STARTING, RECORDING, STOPPING }
    enum class LocalStt { OFF, LOADING, READY, UNAVAILABLE }

    val isActive: Boolean get() = phase != Phase.IDLE
}

/**
 * Foreground service (type microphone) that runs one Live session: mic capture → durable outbox
 * → WebSocket stream, with on-device Whisper drafts alongside. The phone counterpart of the Mac
 * app's `AppModel` start/stop flow. Survives the screen turning off; if it is killed anyway, the
 * outbox keeps the audio and [LiveUploadWorker] finishes the session later.
 */
class LiveSessionService : Service() {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private val main = Handler(Looper.getMainLooper())
    private lateinit var settings: LiveSettings
    private lateinit var repo: SessionRepository

    private var run: Run? = null
    /** Stop tapped while the session was still being created. */
    @Volatile private var stopRequested = false

    /** Everything that belongs to one running session. */
    private class Run(
        val meta: SessionMeta,
        val outbox: Outbox,
        val connection: LiveConnection?,
        val capture: AudioCapture,
        val store: SessionStore,
    ) {
        @Volatile var transcriber: LiveTranscriber? = null
        @Volatile var whisper: WhisperRecognizer? = null
        @Volatile var lastTMs: Long = 0
        @Volatile var lastPartialSentAt: Long = 0
        val markers = ArrayList<LocalMarkdown.Marker>()
        val jobs = ArrayList<Job>()
    }

    private var wakeLock: PowerManager.WakeLock? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        settings = LiveSettings(this)
        repo = SessionRepository(settings.sessionsRoot)
        createNotificationChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_START -> {
                if (!goForeground()) return START_NOT_STICKY
                if (run != null || _ui.value.isActive) return START_NOT_STICKY
                val mode = SessionMode.fromWire(intent.getStringExtra(EXTRA_MODE) ?: "") ?: SessionMode.SOLO
                val title = intent.getStringExtra(EXTRA_TITLE)?.takeIf { it.isNotBlank() }
                val localOnly = intent.getBooleanExtra(EXTRA_LOCAL_ONLY, false)
                stopRequested = false
                _ui.value = LiveUiState(phase = LiveUiState.Phase.STARTING, mode = mode, localOnly = localOnly)
                scope.launch { startSession(mode, title, localOnly) }
            }
            ACTION_STOP -> {
                if (run == null && _ui.value.phase == LiveUiState.Phase.STARTING) stopRequested = true
                else scope.launch { stopSession() }
            }
            ACTION_MARKER -> addMarker(intent.getStringExtra(EXTRA_LABEL) ?: "marker")
            else -> if (run == null) stopSelf()
        }
        // Not sticky: a restarted service would have no capture to resume; the outbox survives.
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        // Killed without a stop (e.g. swiped away with "stop" in the app info). Keep the audio.
        run?.let { r ->
            runCatching { r.capture.stop() }
            r.transcriber?.cancel()
            r.connection?.stop()
            r.outbox.close()
            // Not releasing the recognizer: a cancelled transcription may still be inside it.
            repo.update(r.meta.id) { it.copy(state = SessionMeta.State.PENDING) }
            if (r.meta.serverId != null) LiveUploadWorker.enqueue(this, r.meta.id)
        }
        run = null
        releaseWakeLock()
        if (_ui.value.isActive) _ui.update { it.copy(phase = LiveUiState.Phase.IDLE) }
        scope.cancel()
        super.onDestroy()
    }

    // region Start / stop

    private suspend fun startSession(mode: SessionMode, title: String?, requestedLocalOnly: Boolean) {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            return abortStart("Microphone permission is required")
        }
        if (!requestedLocalOnly && !settings.isConfigured) {
            return abortStart("Set the Jarvis Live device token in Settings, or turn on Local only")
        }
        // Dictation and Live can't share the mic: whichever starts later wins, like Voice to Vault.
        withContext(Dispatchers.Main) { VoiceOverlayService.instance?.cancelActiveRecording() }

        var localOnly = requestedLocalOnly
        var notice: String? = null
        val api = if (settings.isConfigured) settings.apiClient() else null
        var serverId: String? = null
        if (!localOnly && api != null) {
            try {
                serverId = api.createSession(mode, listOf(Channel.MIC), title)
            } catch (e: LiveHttpException) {
                return abortStart("Server rejected the session (HTTP ${e.status})")
            } catch (e: IOException) {
                // No connectivity: record anyway and upload later, rather than losing the moment.
                DebugLog.w(TAG, "createSession failed, recording locally: ${e.message}")
                localOnly = true
                notice = "Server unreachable — recording locally. Upload it when you're back online."
            }
        }

        val id = serverId ?: UUID.randomUUID().toString()
        val meta = SessionMeta(
            id = id, mode = mode, title = title, startedAt = System.currentTimeMillis(),
            localOnly = localOnly, serverId = serverId, state = SessionMeta.State.RECORDING,
        )
        val outbox = try {
            repo.write(meta)
            Outbox.open(settings.sessionsRoot, id)
        } catch (e: IOException) {
            return abortStart("Cannot create session storage: ${e.message}")
        }
        val connection = if (serverId != null && api != null) {
            LiveConnection(api, serverId, settings.deviceName, outbox, scope)
        } else null
        val r = Run(meta, outbox, connection, AudioCapture(this), SessionStore())
        run = r

        connection?.let { conn ->
            r.jobs += scope.launch {
                conn.events.collect { e ->
                    r.store.apply(e)
                    _ui.update { it.copy(session = r.store.snapshot) }
                }
            }
            r.jobs += scope.launch { conn.state.collect { s -> _ui.update { it.copy(connection = s) } } }
        }

        try {
            r.capture.start(
                sink = { samples, pcm, tMs ->
                    r.lastTMs = tMs
                    try {
                        if (connection != null) connection.enqueueAudio(Channel.MIC, pcm, tMs)
                        else outbox.append(Channel.MIC, pcm, tMs)
                    } catch (e: Exception) {
                        DebugLog.e(TAG, "outbox append failed", e)
                    }
                    r.transcriber?.feed(samples, tMs)
                },
                onError = { e -> scope.launch { stopSession("Microphone stopped: ${e.message}") } },
            )
        } catch (e: Exception) {
            run = null
            r.jobs.forEach { it.cancel() }
            connection?.stop()
            outbox.close()
            repo.delete(id)
            return abortStart("Microphone unavailable: ${e.message}")
        }
        connection?.start()
        acquireWakeLock()

        _ui.update {
            it.copy(
                phase = LiveUiState.Phase.RECORDING, sessionId = id, localOnly = localOnly,
                startedElapsed = SystemClock.elapsedRealtime(), message = notice,
                connection = connection?.state?.value ?: ConnState.Idle,
            )
        }
        updateNotification()
        DebugLog.i(TAG, "session $id started mode=${mode.wire} localOnly=$localOnly")

        if (stopRequested) return stopSession()
        r.jobs += scope.launch { loadTranscriber(r) }
        r.jobs += scope.launch {
            while (true) {
                _ui.update { it.copy(unackedMs = outbox.totalUnackedMs(), level = r.capture.level) }
                delay(250)
            }
        }
    }

    private fun loadTranscriber(r: Run) {
        if (!SherpaOnnxSpeechEngine.isModelAvailable(this)) {
            _ui.update { it.copy(localStt = LiveUiState.LocalStt.UNAVAILABLE) }
            return
        }
        _ui.update { it.copy(localStt = LiveUiState.LocalStt.LOADING) }
        val whisper = WhisperRecognizer.create(this)
        if (!whisper.isReady || run !== r) {
            whisper.release()
            _ui.update { it.copy(localStt = LiveUiState.LocalStt.UNAVAILABLE) }
            return
        }
        r.whisper = whisper
        r.transcriber = LiveTranscriber(whisper::transcribe, { onDraft(r, it) })
        _ui.update { it.copy(localStt = LiveUiState.LocalStt.READY) }
    }

    private fun onDraft(r: Run, draft: DraftSegment) {
        r.store.applyDraft(draft)
        _ui.update { it.copy(session = r.store.snapshot) }
        val conn = r.connection ?: return
        // Partials at most every 0.4 s; finals always (same as the Mac).
        val now = SystemClock.elapsedRealtime()
        if (!draft.final && now - r.lastPartialSentAt < PARTIAL_INTERVAL_MS) return
        if (!draft.final) r.lastPartialSentAt = now
        conn.sendDraft(ClientMessage.Draft(draft))
    }

    private fun addMarker(label: String) {
        val r = run ?: return
        val marker = LocalMarkdown.Marker(r.lastTMs, label)
        synchronized(r.markers) { r.markers += marker }
        r.connection?.sendMarker(ClientMessage.Marker(marker.tMs, marker.label))
        _ui.update { it.copy(markers = it.markers + 1) }
    }

    private suspend fun stopSession(error: String? = null) {
        val r = run ?: return
        if (_ui.value.phase == LiveUiState.Phase.STOPPING) return
        _ui.update { it.copy(phase = LiveUiState.Phase.STOPPING, message = error ?: it.message) }
        updateNotification()

        withContext(Dispatchers.IO) {
            r.capture.stop()
            // Release the native recognizer only once no transcription can still be using it.
            if (r.transcriber?.finish() != false) r.whisper?.release()
        }

        var state = SessionMeta.State.PENDING
        var failure: String? = null
        r.connection?.let { conn ->
            when (val terminal = withTimeoutOrNull(END_TIMEOUT_MS) { conn.end() }) {
                is ConnState.Ended -> state = SessionMeta.State.DONE
                is ConnState.Failed -> {
                    state = SessionMeta.State.FAILED
                    failure = terminal.reason
                }
                else -> conn.stop()  // offline: the upload worker finishes it
            }
        }
        r.jobs.forEach { it.cancel() }
        r.outbox.close()
        run = null

        var notePath: String? = null
        if (r.meta.localOnly) {
            notePath = withContext(Dispatchers.IO) { writeLocalNote(r) }
        }
        val meta = r.meta.copy(state = state, error = failure, notePath = notePath)
        repo.write(meta)
        if (state == SessionMeta.State.PENDING && meta.serverId != null) LiveUploadWorker.enqueue(this, meta.id)

        val message = when {
            failure != null -> "Server ended the session: $failure"
            state == SessionMeta.State.PENDING && meta.serverId != null ->
                "Still uploading — it will finish in the background."
            meta.localOnly -> "Saved locally. Upload it from Pending sessions."
            else -> error
        }
        _ui.update {
            it.copy(
                phase = LiveUiState.Phase.IDLE, unackedMs = 0, level = 0f,
                localStt = LiveUiState.LocalStt.OFF, message = message,
            )
        }
        DebugLog.i(TAG, "session ${meta.id} stopped state=$state")
        releaseWakeLock()
        main.post {
            stopForeground(STOP_FOREGROUND_REMOVE)
            stopSelf()
        }
    }

    private fun writeLocalNote(r: Run): String? = runCatching {
        val lines = r.store.finalDrafts().map { LocalMarkdown.Line(it.channel, it.startMs, it.text) }
        val markers = synchronized(r.markers) { r.markers.toList() }
        val text = LocalMarkdown.render(
            r.meta.id, r.meta.mode, listOf(Channel.MIC), r.meta.startedAt, null, lines, markers,
        )
        LocalMarkdown.write(this, LocalMarkdown.fileName(r.meta.startedAt), text)
    }.onFailure { DebugLog.e(TAG, "local note failed", it) }.getOrNull()

    private fun abortStart(message: String) {
        DebugLog.w(TAG, "start aborted: $message")
        _ui.value = LiveUiState(message = message)
        main.post {
            stopForeground(STOP_FOREGROUND_REMOVE)
            stopSelf()
        }
    }

    // endregion

    // region Foreground, notification, wake lock

    private fun goForeground(): Boolean = try {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            startForeground(NOTIF_ID, buildNotification(), ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE)
        } else {
            startForeground(NOTIF_ID, buildNotification())
        }
        true
    } catch (e: Exception) {
        // Android 14 refuses a microphone FGS unless the app is visible.
        DebugLog.e(TAG, "startForeground refused", e)
        _ui.value = LiveUiState(message = "Open Jarvis Voice to start a Live session")
        stopSelf()
        false
    }

    private fun buildNotification(): Notification {
        val open = PendingIntent.getActivity(
            this, 0, Intent(this, LiveActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_IMMUTABLE,
        )
        val stop = PendingIntent.getService(
            this, 1, Intent(this, LiveSessionService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_IMMUTABLE,
        )
        val marker = PendingIntent.getService(
            this, 2, Intent(this, LiveSessionService::class.java).setAction(ACTION_MARKER),
            PendingIntent.FLAG_IMMUTABLE,
        )
        val state = _ui.value
        val text = when (state.phase) {
            LiveUiState.Phase.STOPPING -> getString(R.string.live_notif_finishing)
            else -> if (state.localOnly) getString(R.string.live_notif_local) else getString(R.string.live_notif_streaming)
        }
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle(getString(R.string.live_notif_title))
            .setContentText(text)
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setOngoing(true)
            .setSilent(true)
            .setContentIntent(open)
            .setUsesChronometer(state.phase == LiveUiState.Phase.RECORDING)
            .setWhen(System.currentTimeMillis() - (SystemClock.elapsedRealtime() - state.startedElapsed))
            .addAction(android.R.drawable.ic_menu_edit, getString(R.string.live_marker), marker)
            .addAction(android.R.drawable.ic_media_pause, getString(R.string.live_stop), stop)
            .build()
    }

    private fun updateNotification() {
        getSystemService(NotificationManager::class.java).notify(NOTIF_ID, buildNotification())
    }

    private fun createNotificationChannel() {
        getSystemService(NotificationManager::class.java).createNotificationChannel(
            NotificationChannel(CHANNEL_ID, getString(R.string.live_channel_name), NotificationManager.IMPORTANCE_LOW)
        )
    }

    private fun acquireWakeLock() {
        if (wakeLock?.isHeld == true) return
        wakeLock = (getSystemService(POWER_SERVICE) as PowerManager)
            .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "JarvisVoice::LiveSession")
            .also { it.acquire(MAX_SESSION_MS) }
    }

    private fun releaseWakeLock() {
        wakeLock?.let { if (it.isHeld) it.release() }
        wakeLock = null
    }

    // endregion

    companion object {
        private const val TAG = "LiveSession"
        private const val CHANNEL_ID = "jarvis_live_session"
        private const val NOTIF_ID = 4201
        private const val PARTIAL_INTERVAL_MS = 400L
        /** How long Stop waits for the server before handing off to the upload worker. */
        private const val END_TIMEOUT_MS = 20_000L
        private const val MAX_SESSION_MS = 4 * 60 * 60 * 1000L

        const val ACTION_START = "com.lordmuffin.jarvisvoice.live.START"
        const val ACTION_STOP = "com.lordmuffin.jarvisvoice.live.STOP"
        const val ACTION_MARKER = "com.lordmuffin.jarvisvoice.live.MARKER"
        private const val EXTRA_MODE = "mode"
        private const val EXTRA_TITLE = "title"
        private const val EXTRA_LOCAL_ONLY = "local_only"
        private const val EXTRA_LABEL = "label"

        private val _ui = MutableStateFlow(LiveUiState())
        val ui: StateFlow<LiveUiState> = _ui

        /** True while a session owns the microphone. */
        val isRecording: Boolean get() = _ui.value.isActive

        fun start(context: Context, mode: SessionMode, title: String?, localOnly: Boolean) {
            val intent = Intent(context, LiveSessionService::class.java)
                .setAction(ACTION_START)
                .putExtra(EXTRA_MODE, mode.wire)
                .putExtra(EXTRA_TITLE, title)
                .putExtra(EXTRA_LOCAL_ONLY, localOnly)
            ContextCompat.startForegroundService(context, intent)
        }

        fun stop(context: Context) {
            context.startService(Intent(context, LiveSessionService::class.java).setAction(ACTION_STOP))
        }

        fun marker(context: Context, label: String = "marker") {
            context.startService(
                Intent(context, LiveSessionService::class.java).setAction(ACTION_MARKER).putExtra(EXTRA_LABEL, label)
            )
        }

        fun clearMessage() = _ui.update { it.copy(message = null) }
    }
}
