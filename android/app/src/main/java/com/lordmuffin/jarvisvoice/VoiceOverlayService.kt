package com.lordmuffin.jarvisvoice

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.pm.ServiceInfo
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.graphics.Color
import android.graphics.PixelFormat
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import android.os.SystemClock
import android.view.Gravity
import android.view.LayoutInflater
import android.view.MotionEvent
import android.view.View
import android.view.ViewConfiguration
import android.view.WindowManager
import android.view.accessibility.AccessibilityNodeInfo
import android.view.animation.AnimationUtils
import android.widget.ImageView
import android.widget.ProgressBar
import android.widget.Toast
import androidx.core.app.NotificationCompat
import com.lordmuffin.jarvisvoice.speech.SherpaOnnxSpeechEngine
import com.lordmuffin.jarvisvoice.speech.SpeechEngine
import com.lordmuffin.jarvisvoice.speech.SpeechEngineFactory
import com.lordmuffin.jarvisvoice.ui.AudioWaveformView

enum class OverlayState { IDLE, RECORDING, PROCESSING, DONE }

class VoiceOverlayService : Service() {

    companion object {
        const val CHANNEL_ID              = "jarvis_voice_overlay"
        const val CHANNEL_TRANSCRIPT_ID   = "jarvis_transcript"
        const val NOTIF_ID                = 1
        const val NOTIF_TRANSCRIPT_ID     = 2
        const val ACTION_OPEN_SETTINGS    = "com.lordmuffin.jarvisvoice.OPEN_SETTINGS"
        const val PREF_FILE               = "jarvis_prefs"
        const val KEY_CLIPBOARD_NOTIFY    = "clipboard_notify"
        const val KEY_SCREEN_ALWAYS_ON    = "screen_always_on"
        var lastFocusedNode: AccessibilityNodeInfo? = null
        var instance: VoiceOverlayService? = null
    }

    private lateinit var windowManager: WindowManager
    private lateinit var overlayView: View
    private lateinit var waveformView: AudioWaveformView
    private lateinit var micIcon: ImageView
    private lateinit var dotIdle: View
    private lateinit var progressSpinner: ProgressBar
    var speechEngine: SpeechEngine? = null
        private set
    private var state = OverlayState.IDLE

    private lateinit var historyManager: DictationHistoryManager
    private lateinit var dictManager: CustomDictionaryManager

    // Drag state
    private var initialX = 0
    private var initialY = 0
    private var initialTouchX = 0f
    private var initialTouchY = 0f
    private var isDragging = false
    private var holdModeActive = false
    private var sessionStartMs = 0L
    private var wakeLock: PowerManager.WakeLock? = null
    private var longPressRunnable: Runnable? = null
    private val longPressHandler = Handler(Looper.getMainLooper())

    // Screen-off recording continuity
    @Volatile private var screenOff = false
    private var hiddenWhileRecording = false
    private val screenStateReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            when (intent.action) {
                Intent.ACTION_SCREEN_OFF -> {
                    screenOff = true
                    DebugLog.i("Overlay", "screen OFF — screenOff=true state=$state")
                    when (state) {
                        OverlayState.RECORDING -> {
                            // FLAG_KEEP_SCREEN_ON prevents timeout-off, so this is a power-button press.
                            // Stop the recording and note why — we cannot continue recording on a locked screen.
                            DebugLog.i("Overlay", "screen locked by user during recording — stopping and processing partial transcript")
                            Handler(Looper.getMainLooper()).post { stopRecording() }
                        }
                        OverlayState.PROCESSING -> {
                            // Whisper is still running after a stop — hide overlay, let it finish.
                            hiddenWhileRecording = true
                            overlayView.visibility = View.GONE
                        }
                        else -> { /* IDLE/DONE — nothing to do */ }
                    }
                }
                Intent.ACTION_SCREEN_ON -> {
                    screenOff = false
                    DebugLog.i("Overlay", "screen ON — restoring overlay hiddenWhileRecording=$hiddenWhileRecording")
                    if (hiddenWhileRecording) {
                        hiddenWhileRecording = false
                        overlayView.visibility = View.VISIBLE
                    }
                }
            }
        }
    }

    private lateinit var params: WindowManager.LayoutParams

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        instance       = this
        runCatching { PersistentStorage.migrateIfNeeded(this) }
            .onFailure { DebugLog.e("Service", "migration failed (non-fatal)", it) }
        DebugLog.init(this)
        DebugLog.i("Service", "onCreate storage=${PersistentStorage.storageLabel(this)}")
        historyManager = DictationHistoryManager(this)
        dictManager    = CustomDictionaryManager(this)
        LlmEnhancer.bindContext(this)
        createNotificationChannel()
        // Android 14 blocks FGS type=microphone when the app is not in an eligible foreground
        // state (no visible activity, no bound foreground client). This happens on system-
        // initiated restarts (START_STICKY, OOM recovery). Catch and stop cleanly — the
        // accessibility service or the user opening the app will restart us in valid state.
        try {
            if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
                startForeground(NOTIF_ID, buildNotification(),
                    ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE)
            } else {
                startForeground(NOTIF_ID, buildNotification())
            }
        } catch (e: SecurityException) {
            DebugLog.e("Service", "startForeground blocked — not in eligible state, stopping", e)
            stopSelf()
            return
        }
        setupOverlay()
        speechEngine = SpeechEngineFactory.create(this)
        loadLlmIfConfigured()
        // Service started cleanly — tell the crash guard so the next app launch doesn't
        // cancel all WorkManager jobs unnecessarily.
        (application as JarvisApp).markCleanLaunch()
        val screenFilter = IntentFilter().apply {
            addAction(Intent.ACTION_SCREEN_OFF)
            addAction(Intent.ACTION_SCREEN_ON)
        }
        // Android 14 (API 34) requires RECEIVER_NOT_EXPORTED even for system broadcasts
        // when the app targets API 34+. Without this the service crashes immediately on
        // every start, causing a tight START_STICKY restart loop.
        if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.TIRAMISU) {
            registerReceiver(screenStateReceiver, screenFilter, Context.RECEIVER_NOT_EXPORTED)
        } else {
            registerReceiver(screenStateReceiver, screenFilter)
        }
    }

    private fun loadLlmIfConfigured() {
        val llmMgr  = LlmModelManager(this)
        val config  = llmMgr.getActiveConfig()
        if (config == null) {
            DebugLog.i("LlmInit", "no active model (model=none)")
            return
        }
        val modelFile = llmMgr.modelFile(config)
        val installed = llmMgr.isInstalled(config)
        DebugLog.i("LlmInit", "active=${config.id} path=${modelFile.absolutePath} exists=${modelFile.exists()} size=${modelFile.length()} installed=$installed npuOnly=${config.npuOnly}")
        if (!installed) return
        val nativeLibDir = applicationInfo.nativeLibraryDir
        val npuOnly = config.npuOnly
        Thread {
            val ok = runCatching {
                LlmEnhancer.init(modelFile, config.id, nativeLibDir, npuOnly)
            }.getOrElse { t ->
                DebugLog.e("Service", "LLM init failed on background thread", t)
                false
            }
            if (!ok) {
                val msg = if (npuOnly)
                    "${config.displayName} requires NPU hardware — not available. Switch to a CPU model in Settings → LLM Model."
                else
                    "${config.displayName} failed to load. Switch to another model in Settings → LLM Model."
                Handler(Looper.getMainLooper()).post {
                    Toast.makeText(this@VoiceOverlayService, msg, Toast.LENGTH_LONG).show()
                }
            }
        }.start()
    }

    fun reloadLlmModel() {
        LlmEnhancer.destroy()
        LlmEnhancer.clearCrashSentinel()
        loadLlmIfConfigured()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_OPEN_SETTINGS) {
            startActivity(
                Intent(this, SettingsActivity::class.java)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            )
        }
        // Always NOT_STICKY — the service is user-initiated (app open / accessibility event).
        // START_STICKY causes rapid crash loops when the LLM backend init throws a native error
        // that runCatching can't intercept. If the service dies, the user opens the app to restart.
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        DebugLog.i("Service", "onDestroy — service is being killed")
        super.onDestroy()
        runCatching { unregisterReceiver(screenStateReceiver) }
        instance = null
        speechEngine?.destroy()
        historyManager.close()
        if (::overlayView.isInitialized) {
            runCatching { windowManager.removeView(overlayView) }
        }
    }

    private fun setupOverlay() {
        windowManager = getSystemService(WINDOW_SERVICE) as WindowManager

        overlayView     = LayoutInflater.from(this).inflate(R.layout.overlay_pill, null)
        waveformView    = overlayView.findViewById(R.id.waveform)
        micIcon         = overlayView.findViewById(R.id.mic_icon)
        dotIdle         = overlayView.findViewById(R.id.dot_idle)
        progressSpinner = overlayView.findViewById(R.id.progress_spinner)

        val density    = resources.displayMetrics.density
        val idleWidthPx = (120 * density).toInt()
        val heightPx    = (40 * density).toInt()

        params = WindowManager.LayoutParams(
            idleWidthPx,
            heightPx,
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                    WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
            PixelFormat.TRANSLUCENT
        ).apply {
            gravity = Gravity.BOTTOM or Gravity.CENTER_HORIZONTAL
            x = 0
            y = (80 * density).toInt()
        }

        overlayView.setOnTouchListener(::handleTouch)
        windowManager.addView(overlayView, params)
        setIdleState()
        overlayView.visibility = View.GONE
    }

    @Suppress("UNUSED_PARAMETER")
    private fun handleTouch(v: View, event: MotionEvent): Boolean {
        when (event.action) {
            MotionEvent.ACTION_DOWN -> {
                isDragging = false
                initialX = params.x
                initialY = params.y
                initialTouchX = event.rawX
                initialTouchY = event.rawY
                longPressRunnable = Runnable {
                    if (state == OverlayState.IDLE) {
                        holdModeActive = true
                        startRecording(holdMode = true)
                    }
                }
                longPressHandler.postDelayed(
                    longPressRunnable!!,
                    ViewConfiguration.getLongPressTimeout().toLong()
                )
            }
            MotionEvent.ACTION_MOVE -> {
                val dx = (event.rawX - initialTouchX).toInt()
                val dy = (event.rawY - initialTouchY).toInt()
                if (!isDragging && (Math.abs(dx) > 8 || Math.abs(dy) > 8)) {
                    isDragging = true
                    longPressRunnable?.let { longPressHandler.removeCallbacks(it) }
                }
                if (isDragging) {
                    params.x = initialX + dx
                    params.y = initialY - dy
                    runCatching { windowManager.updateViewLayout(overlayView, params) }
                }
            }
            MotionEvent.ACTION_UP -> {
                longPressRunnable?.let { longPressHandler.removeCallbacks(it) }
                // Hold-to-record release: stop immediately, don't process as tap
                if (holdModeActive) {
                    holdModeActive = false
                    if (state == OverlayState.RECORDING) stopRecording()
                    return true
                }
                if (!isDragging) {
                    when (state) {
                        OverlayState.IDLE       -> startRecording(holdMode = false)
                        OverlayState.RECORDING  -> stopRecording()
                        OverlayState.PROCESSING -> { /* Whisper is working — ignore tap */ }
                        OverlayState.DONE       -> setIdleState()
                    }
                } else if (state == OverlayState.RECORDING) {
                    stopRecording()
                }
            }
        }
        return true
    }

    /** Release the microphone so another component (e.g. VaultCaptureActivity) can use it. */
    fun cancelActiveRecording() {
        if (state == OverlayState.RECORDING) {
            stopRecording()
        }
    }

    fun startRecording(holdMode: Boolean = false) {
        DebugLog.i("Overlay", "startRecording holdMode=$holdMode engine=${speechEngine?.javaClass?.simpleName}")
        sessionStartMs = SystemClock.elapsedRealtime()
        if (wakeLock == null || wakeLock?.isHeld == false) {
            wakeLock = (getSystemService(POWER_SERVICE) as PowerManager)
                .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "JarvisVoice::Recording")
                .also { it.acquire(10 * 60 * 1000L) } // 10 min max — keeps CPU alive if screen forced off
        }
        // Keep screen on and suppress auto-lock for the duration of recording + processing.
        // Power-button press WILL still turn the screen off; that case is handled in screenStateReceiver.
        params.flags = params.flags or WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON
        runCatching { windowManager.updateViewLayout(overlayView, params) }
        state = OverlayState.RECORDING
        dotIdle.clearAnimation()
        dotIdle.visibility = View.GONE
        progressSpinner.visibility = View.GONE
        micIcon.visibility = View.GONE
        waveformView.barColor = Color.parseColor("#00F5D4")
        waveformView.visibility = View.VISIBLE
        waveformView.startAnimation()
        overlayView.setBackgroundResource(R.drawable.pill_background_red)
        // Widen pill for recording state
        val density = resources.displayMetrics.density
        params.width = (144 * density).toInt()
        runCatching { windowManager.updateViewLayout(overlayView, params) }

        speechEngine?.startListening(
            onPartial = { /* compact mode — no partial visual */ },
            onFinal   = { final ->
                Handler(Looper.getMainLooper()).post { handleFinalTranscript(final) }
            },
            onError   = {
                Handler(Looper.getMainLooper()).post { setIdleState() }
            },
            holdMode  = holdMode
        )
    }

    private fun stopRecording() {
        // Instant visual feedback — don't wait for Whisper to finish
        state = OverlayState.PROCESSING
        waveformView.stopAnimation()
        waveformView.visibility = View.GONE
        dotIdle.clearAnimation()
        dotIdle.visibility = View.GONE
        micIcon.visibility = View.GONE
        progressSpinner.visibility = View.VISIBLE
        overlayView.setBackgroundResource(R.drawable.pill_background)
        // Restore idle width during processing
        val density = resources.displayMetrics.density
        params.width = (120 * density).toInt()
        runCatching { windowManager.updateViewLayout(overlayView, params) }
        DebugLog.i("Overlay", "stopRecording → PROCESSING (Whisper running in background)")
        speechEngine?.stopListening()
    }

    private fun handleFinalTranscript(text: String) {
        val elapsedMs = SystemClock.elapsedRealtime() - sessionStartMs
        DebugLog.i("Overlay", "handleFinalTranscript elapsedMs=$elapsedMs textLen=${text.length} blank=${text.isBlank()}")
        val filtered = TranscriptProcessor.process(text) ?: run {
            DebugLog.i("Overlay", "transcript filtered/discarded — setIdleState")
            setIdleState()
            return
        }

        val withDict = dictManager.applyTo(filtered)
        val sttBackend = (speechEngine as? SherpaOnnxSpeechEngine)?.activeProvider ?: ""

        // ─── PASS 1 · Whisper STT ────────────────────────────────────────────────
        val pass1Words = withDict.trim().split(Regex("\\s+")).filter { it.isNotBlank() }
        DebugLog.i("PASS1", "━━━ PASS 1 · Whisper STT ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        DebugLog.i("PASS1", "  text  : \"${withDict.take(300)}${if (withDict.length > 300) "…" else ""}\"")
        DebugLog.i("PASS1", "  words : ${pass1Words.size}  |  chars : ${withDict.length}")
        DebugLog.i("PASS1", "  recMs : $elapsedMs ms  (recording + transcription combined)")
        DebugLog.i("PASS1", "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        // ────────────────────────────────────────────────────────────────────────

        // LLM enhancement is blocking (seconds). Run on background thread, then rejoin main.
        if (LlmEnhancer.isReady()) {
            Thread {
                // ─── PASS 2 · YellowLab Enhancement ─────────────────────────────────
                val tLlm = SystemClock.elapsedRealtime()
                DebugLog.i("PASS2", "━━━ PASS 2 · YellowLab Enhancement ━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                DebugLog.i("PASS2", "  in   : \"${withDict.take(300)}${if (withDict.length > 300) "…" else ""}\"")
                DebugLog.i("PASS2", "  inW  : ${pass1Words.size}  |  inC : ${withDict.length}")
                val enhanced = LlmEnhancer.enhance(withDict)
                val llmMs = SystemClock.elapsedRealtime() - tLlm
                val pass2Words = enhanced.trim().split(Regex("\\s+")).filter { it.isNotBlank() }
                DebugLog.i("PASS2", "  out  : \"${enhanced.take(300)}${if (enhanced.length > 300) "…" else ""}\"")
                DebugLog.i("PASS2", "  outW : ${pass2Words.size}  |  outC : ${enhanced.length}")
                DebugLog.i("PASS2", "  ΔW   : ${pass2Words.size - pass1Words.size}  |  ΔC : ${enhanced.length - withDict.length}")
                DebugLog.i("PASS2", "  llmMs: $llmMs ms")
                DebugLog.i("PASS2", "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                // ────────────────────────────────────────────────────────────────────
                val modelName = LlmModelManager(this@VoiceOverlayService).getActiveConfig()?.displayName ?: ""
                val llmBackend = LlmEnhancer.activeBackend
                Handler(Looper.getMainLooper()).post { finalizeAndInject(withDict, enhanced, elapsedMs, modelName, llmMs, sttBackend, llmBackend) }
            }.start()
        } else {
            DebugLog.i("PASS2", "━━━ PASS 2 · YellowLab Enhancement ━━━ SKIPPED (no model loaded)")
            finalizeAndInject(withDict, withDict, elapsedMs, sttBackend = sttBackend)
        }
    }

    private fun finalizeAndInject(rawText: String, finalText: String, elapsedMs: Long,
                                   llmModel: String = "", llmMs: Long = 0L,
                                   sttBackend: String = "", llmBackend: String = "") {
        val session = historyManager.saveSession(rawText, finalText, elapsedMs, llmModel, llmMs, sttBackend, llmBackend)

        state = OverlayState.DONE
        waveformView.stopAnimation()
        progressSpinner.visibility = View.GONE
        dotIdle.clearAnimation()
        dotIdle.visibility = View.VISIBLE
        overlayView.setBackgroundResource(R.drawable.pill_background_accent)

        DebugLog.i("Overlay", "inject node=${lastFocusedNode != null} final=\"${finalText.take(60)}\"")
        TextInjector.inject(lastFocusedNode, finalText)

        val prefs = getSharedPreferences(PREF_FILE, android.content.Context.MODE_PRIVATE)
        if (prefs.getBoolean(KEY_CLIPBOARD_NOTIFY, true)) {
            fireTranscriptNotification(finalText)
        }

        val wasEnhanced = rawText != finalText
        val msg = when {
            lastFocusedNode == null && session.wordCount > 0 ->
                "Copied · ${session.wordCount} words · ${"%.0f".format(session.wpm)} wpm${if (wasEnhanced) " · Enhanced" else ""}"
            lastFocusedNode == null ->
                "Copied to clipboard"
            session.wordCount > 0 ->
                "${session.wordCount} words · ${"%.0f".format(session.wpm)} wpm${if (wasEnhanced) " · Enhanced" else ""}"
            else -> null
        }
        msg?.let { Toast.makeText(this, it, Toast.LENGTH_SHORT).show() }

        Handler(Looper.getMainLooper()).postDelayed({ setIdleState() }, 400)
    }

    private fun setIdleState() {
        state = OverlayState.IDLE
        micIcon.visibility = View.GONE
        waveformView.visibility = View.GONE
        waveformView.stopAnimation()
        progressSpinner.visibility = View.GONE
        // Restore idle width
        val density = resources.displayMetrics.density
        params.width = (120 * density).toInt()
        runCatching { windowManager.updateViewLayout(overlayView, params) }
        // Show breathing dot
        dotIdle.visibility = View.VISIBLE
        val breatheAnim = AnimationUtils.loadAnimation(this, R.anim.breathe)
        dotIdle.startAnimation(breatheAnim)
        overlayView.setBackgroundResource(R.drawable.pill_background)
        // Screen-on is only held during active recording — always release on idle.
        params.flags = params.flags and WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON.inv()
        runCatching { windowManager.updateViewLayout(overlayView, params) }
        if (wakeLock?.isHeld == true) {
            wakeLock?.release()
            DebugLog.i("Overlay", "wake lock released")
        }
    }

    fun isScreenOff(): Boolean = screenOff

    fun showOverlay() {
        overlayView.visibility = View.VISIBLE
    }

    fun hideOverlay() {
        if (state == OverlayState.RECORDING || state == OverlayState.PROCESSING) {
            if (screenOff) {
                // Screen just went off — preserve recording, only hide visuals.
                // screenStateReceiver.ACTION_SCREEN_OFF already set hiddenWhileRecording.
                DebugLog.i("Overlay", "hideOverlay skipped stopRecording — screen is off, recording continues")
                overlayView.visibility = View.GONE
                return
            }
            // Keyboard dismissed by user action while screen is on — stop recording normally.
            stopRecording()
        }
        overlayView.visibility = View.GONE
    }

    fun setScreenAlwaysOn(on: Boolean) {
        getSharedPreferences(PREF_FILE, android.content.Context.MODE_PRIVATE)
            .edit().putBoolean(KEY_SCREEN_ALWAYS_ON, on).apply()
        if (on) {
            params.flags = params.flags or WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON
        } else if (state == OverlayState.IDLE) {
            params.flags = params.flags and WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON.inv()
        }
        runCatching { windowManager.updateViewLayout(overlayView, params) }
    }

    fun reloadSpeechEngine() {
        val wasRecording = state == OverlayState.RECORDING
        if (wasRecording) stopRecording()
        speechEngine?.destroy()
        speechEngine = SpeechEngineFactory.create(this)
    }

    private fun fireTranscriptNotification(text: String) {
        val preview = if (text.length > 80) text.take(80) + "…" else text
        val notif = NotificationCompat.Builder(this, CHANNEL_TRANSCRIPT_ID)
            .setContentTitle("Copied to clipboard")
            .setContentText(preview)
            .setStyle(NotificationCompat.BigTextStyle().bigText(text))
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setAutoCancel(true)
            .build()
        getSystemService(NotificationManager::class.java).notify(NOTIF_TRANSCRIPT_ID, notif)
    }

    private fun buildNotification(): Notification {
        val settingsPi = PendingIntent.getService(
            this, 0,
            Intent(this, VoiceOverlayService::class.java).setAction(ACTION_OPEN_SETTINGS),
            PendingIntent.FLAG_IMMUTABLE
        )
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("Jarvis Voice")
            .setContentText("Tap mic when keyboard appears to dictate")
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setOngoing(true)
            .setSilent(true)
            .addAction(android.R.drawable.ic_menu_preferences, "Settings", settingsPi)
            .build()
    }

    private fun createNotificationChannel() {
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(
            NotificationChannel(CHANNEL_ID, "Jarvis Voice Overlay", NotificationManager.IMPORTANCE_LOW)
                .also { it.description = "Persistent notification for dictation overlay" }
        )
        nm.createNotificationChannel(
            NotificationChannel(CHANNEL_TRANSCRIPT_ID, "Dictation Clipboard Alerts", NotificationManager.IMPORTANCE_DEFAULT)
                .also { it.description = "Shows when dictation text is copied to clipboard" }
        )
    }
}
