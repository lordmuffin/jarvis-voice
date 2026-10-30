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
import android.view.ContextThemeWrapper
import android.content.pm.PackageManager
import android.Manifest
import androidx.core.content.ContextCompat
import com.lordmuffin.jarvisvoice.pill.DictationPill
import com.lordmuffin.jarvisvoice.pill.PillAction
import com.lordmuffin.jarvisvoice.pill.PillError
import com.lordmuffin.jarvisvoice.pill.PillState
import com.lordmuffin.jarvisvoice.pill.PillView
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import android.widget.Toast
import androidx.core.app.NotificationCompat
import kotlinx.coroutines.flow.MutableStateFlow
import com.lordmuffin.jarvisvoice.live.LiveSessionService
import com.lordmuffin.jarvisvoice.speech.ListenMode
import com.lordmuffin.jarvisvoice.speech.SherpaOnnxSpeechEngine
import com.lordmuffin.jarvisvoice.speech.SpeechEngine
import com.lordmuffin.jarvisvoice.speech.SpeechEngineFactory

class VoiceOverlayService : Service() {

    companion object {
        const val CHANNEL_ID              = "jarvis_voice_overlay"
        const val CHANNEL_TRANSCRIPT_ID   = "jarvis_transcript"
        const val NOTIF_ID                = 1
        const val NOTIF_TRANSCRIPT_ID     = 2
        const val NOTIF_RESTORE_ID        = 3
        const val ACTION_OPEN_SETTINGS    = "com.lordmuffin.jarvisvoice.OPEN_SETTINGS"
        const val ACTION_CAR_START        = "com.lordmuffin.jarvisvoice.CAR_START"
        const val ACTION_CAR_STOP         = "com.lordmuffin.jarvisvoice.CAR_STOP"
        const val ACTION_CAR_MUTE         = "com.lordmuffin.jarvisvoice.CAR_MUTE"
        const val PREF_FILE               = "jarvis_prefs"
        const val KEY_CLIPBOARD_NOTIFY    = "clipboard_notify"
        const val KEY_SCREEN_ALWAYS_ON    = "screen_always_on"
        var lastFocusedNode: AccessibilityNodeInfo? = null
        var instance: VoiceOverlayService? = null

        const val KEY_PILL_X              = "pill_x"
        const val KEY_PILL_Y              = "pill_y"

        // Shared state consumed by JarvisCarScreen — updated from the main thread. The pill's own
        // state is DictationPill.model.
        val carLastTextFlow = MutableStateFlow("")
        val carMutedFlow    = MutableStateFlow(false)
    }

    private lateinit var windowManager: WindowManager
    private lateinit var overlayView: PillView
    private val pill get() = DictationPill.model
    private val uiScope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val resetHandler = Handler(Looper.getMainLooper())
    var speechEngine: SpeechEngine? = null
        private set
    private val isListening get() = pill.state.value is PillState.Listening

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
                    DebugLog.i("Overlay", "screen OFF — screenOff=true state=${pill.state.value}")
                    when (pill.state.value) {
                        is PillState.Listening -> {
                            // FLAG_KEEP_SCREEN_ON prevents timeout-off, so this is a power-button press.
                            // Stop the recording and note why — we cannot continue recording on a locked screen.
                            DebugLog.i("Overlay", "screen locked by user during recording — stopping and processing partial transcript")
                            Handler(Looper.getMainLooper()).post { stopRecording() }
                        }
                        PillState.Processing -> {
                            // Whisper is still running after a stop — hide overlay, let it finish.
                            hiddenWhileRecording = true
                            overlayView.visibility = View.GONE
                        }
                        else -> { /* idle, done, error — nothing to do */ }
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
            postRestoreNotification()
            stopSelf()
            return
        }
        setupOverlay()
        speechEngine = SpeechEngineFactory.create(this)
        if (speechEngine is com.lordmuffin.jarvisvoice.speech.AndroidSpeechEngine && !SherpaOnnxSpeechEngine.isModelAvailable(this) &&
            !android.speech.SpeechRecognizer.isRecognitionAvailable(this)) {
            DebugLog.e("Service", "no speech model and no system recognizer — dictation cannot run")
        }
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

    /** Also called when a Live session ends: the session unloads the LLM to free memory for Whisper. */
    fun loadLlmIfConfigured() {
        if (LiveSessionService.isRecording) {
            DebugLog.i("LlmInit", "Live session running — LLM load deferred until it ends")
            return
        }
        val llmMgr  = LlmModelManager(this)
        val config  = llmMgr.getActiveConfig()
        if (config == null) {
            DebugLog.i("LlmInit", "no active model (model=none)")
            return
        }

        if (config.isAiCore) {
            DebugLog.i("LlmInit", "active=aicore — initializing Android AI Core (Gemini Nano)")
            Thread {
                val ok = AiCoreEnhancer.initialize()
                if (!ok) {
                    Handler(Looper.getMainLooper()).post {
                        Toast.makeText(this@VoiceOverlayService,
                            getString(R.string.models_overlay_gemini_unavailable),
                            Toast.LENGTH_LONG).show()
                    }
                }
            }.start()
            return
        }

        val modelFile = llmMgr.modelFile(config)
        val installed = llmMgr.isInstalled(config)
        DebugLog.i("LlmInit", "active=${config.id} path=${modelFile.absolutePath} exists=${modelFile.exists()} size=${modelFile.length()} installed=$installed npuOnly=${config.npuOnly}")
        if (!installed) return
        val nativeLibDir = applicationInfo.nativeLibraryDir
        val npuOnly = config.npuOnly
        Thread {
            // A Live session may have started since the check above.
            if (LiveSessionService.isRecording) return@Thread
            val ok = runCatching {
                LlmEnhancer.init(modelFile, config.id, nativeLibDir, npuOnly)
            }.getOrElse { t ->
                DebugLog.e("Service", "LLM init failed on background thread", t)
                false
            }
            if (!ok) {
                val msg = if (npuOnly)
                    getString(R.string.models_overlay_npu_required, config.displayName)
                else
                    getString(R.string.models_overlay_load_failed, config.displayName)
                Handler(Looper.getMainLooper()).post {
                    Toast.makeText(this@VoiceOverlayService, msg, Toast.LENGTH_LONG).show()
                }
            }
        }.start()
    }

    fun reloadLlmModel() {
        LlmEnhancer.destroy()
        LlmEnhancer.clearCrashSentinel()
        AiCoreEnhancer.destroy()
        loadLlmIfConfigured()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_OPEN_SETTINGS -> startActivity(
                Intent(this, SettingsActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            )
            ACTION_CAR_START -> if (!carMutedFlow.value) startRecording(holdMode = false)
            ACTION_CAR_STOP  -> if (isListening) stopRecording()
            ACTION_CAR_MUTE  -> carMutedFlow.value = !carMutedFlow.value
        }
        // Always NOT_STICKY — the service is user-initiated (app open / accessibility event).
        // START_STICKY causes rapid crash loops when the LLM backend init throws a native error
        // that runCatching can't intercept. If the service dies, the user opens the app to restart.
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        DebugLog.i("Service", "onDestroy — service is being killed")
        super.onDestroy()
        uiScope.cancel()
        resetHandler.removeCallbacksAndMessages(null)
        runCatching { unregisterReceiver(screenStateReceiver) }
        instance = null
        speechEngine?.destroy()
        runCatching { historyManager.close() }
        if (::overlayView.isInitialized) {
            runCatching { windowManager.removeView(overlayView) }
        }
    }

    private fun setupOverlay() {
        windowManager = getSystemService(WINDOW_SERVICE) as WindowManager

        // A Service has no activity theme; wrap it so the pill's inst_* colours follow the system day/night.
        overlayView = LayoutInflater.from(ContextThemeWrapper(this, R.style.Theme_Jarvis))
            .inflate(R.layout.overlay_pill, null) as PillView

        val density = resources.displayMetrics.density
        val prefs   = getSharedPreferences(PREF_FILE, MODE_PRIVATE)

        params = WindowManager.LayoutParams(
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                    WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
            PixelFormat.TRANSLUCENT
        ).apply {
            gravity = Gravity.BOTTOM or Gravity.CENTER_HORIZONTAL
            // Where the user last dragged it; the default sits 80dp above the bottom edge.
            x = prefs.getInt(KEY_PILL_X, 0)
            y = prefs.getInt(KEY_PILL_Y, (80 * density).toInt())
        }

        overlayView.setOnTouchListener(::handleTouch)
        windowManager.addView(overlayView, params)
        overlayView.visibility = View.GONE
        pill.reset()
        applyIdleWindow()
        overlayView.render(pill.state.value)

        // The pill view and the Android Auto screen both follow the shared model.
        uiScope.launch {
            pill.state.collect { st ->
                overlayView.render(st)
                runCatching { windowManager.updateViewLayout(overlayView, params) }
                resetHandler.removeCallbacksAndMessages(null)
                st.autoResetMs?.let { ms ->
                    resetHandler.postDelayed({ if (pill.state.value === st) setIdleState() }, ms)
                }
            }
        }
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
                    if (pill.state.value === PillState.Idle) {
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
                val slop = ViewConfiguration.get(this).scaledTouchSlop
                if (!isDragging && (Math.abs(dx) > slop || Math.abs(dy) > slop)) {
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
                    if (isListening) stopRecording()
                    return true
                }
                if (!isDragging) {
                    v.performClick()
                    onPillTap()
                } else {
                    getSharedPreferences(PREF_FILE, MODE_PRIVATE).edit()
                        .putInt(KEY_PILL_X, params.x).putInt(KEY_PILL_Y, params.y).apply()
                    if (isListening) stopRecording()
                }
            }
        }
        return true
    }

    private fun onPillTap() {
        when (val st = pill.state.value) {
            PillState.Idle         -> startRecording(holdMode = false)
            is PillState.Listening -> stopRecording()
            PillState.Processing   -> { /* the transcriber is working — ignore the tap */ }
            is PillState.Done      -> setIdleState()
            is PillState.Error     -> when (st.kind.tapAction) {
                PillAction.RETRY -> startRecording(holdMode = false)
                PillAction.OPEN_SETTINGS -> {
                    setIdleState()
                    startActivity(Intent(android.provider.Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                        android.net.Uri.fromParts("package", packageName, null)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                }
                PillAction.OPEN_STT_SETTINGS -> {
                    setIdleState()
                    startActivity(Intent(this, SttModelManagerActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                }
                PillAction.NONE -> { /* informational; it clears itself */ }
            }
        }
    }

    /** Release the microphone so another component (e.g. LiveSessionService) can use it. */
    fun cancelActiveRecording() {
        if (isListening) {
            stopRecording()
        }
    }

    fun startRecording(holdMode: Boolean = false) {
        DebugLog.i("Overlay", "startRecording holdMode=$holdMode engine=${speechEngine?.javaClass?.simpleName}")
        if (LiveSessionService.isRecording) {
            // A Live session owns the microphone; dictation would only capture silence.
            pill.fail(PillError.MIC_BUSY)
            return
        }
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            DebugLog.e("Overlay", "RECORD_AUDIO not granted")
            pill.fail(PillError.MIC_OFF)
            return
        }
        val engine = speechEngine
        if (engine == null) {
            DebugLog.e("Overlay", "no speech engine")
            pill.fail(PillError.ENGINE_UNAVAILABLE)
            return
        }
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
        pill.startListening(sessionStartMs, holdMode)

        try {
            engine.startListening(
                onPartial = { /* compact mode — no partial visual */ },
                onFinal   = { final ->
                    Handler(Looper.getMainLooper()).post { handleFinalTranscript(final) }
                },
                onError   = { code ->
                    Handler(Looper.getMainLooper()).post { failDictation(PillError.fromEngineError(code)) }
                },
                // Tap dictation listens through pauses until the next tap (or a minute of silence).
                mode      = if (holdMode) ListenMode.HOLD else ListenMode.DICTATION
            )
        } catch (e: SecurityException) {
            DebugLog.e("Overlay", "recorder refused to start", e)
            runCatching { engine.cancelListening() }
            failDictation(PillError.MIC_OFF)
        } catch (e: IllegalStateException) {
            DebugLog.e("Overlay", "recorder could not start", e)
            runCatching { engine.cancelListening() }
            failDictation(PillError.MIC_OFF)
        }
    }

    /** Shows [kind] on the pill and lets go of the screen and CPU holds. */
    private fun failDictation(kind: PillError) {
        DebugLog.e("Overlay", "dictation failed: $kind")
        releaseHolds()
        pill.fail(kind)
    }

    private fun stopRecording() {
        // Instant visual feedback — don't wait for Whisper to finish
        pill.stopListening()
        DebugLog.i("Overlay", "stopRecording → PROCESSING (Whisper running in background)")
        speechEngine?.stopListening()
    }

    private fun handleFinalTranscript(text: String) {
        val elapsedMs = SystemClock.elapsedRealtime() - sessionStartMs
        DebugLog.i("Overlay", "handleFinalTranscript elapsedMs=$elapsedMs textLen=${text.length} blank=${text.isBlank()}")
        val filtered = TranscriptProcessor.process(text) ?: run {
            DebugLog.i("Overlay", "transcript filtered/discarded — nothing heard")
            failDictation(PillError.NO_SPEECH)
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
        if (AiCoreEnhancer.isReady() || LlmEnhancer.isReady()) {
            Thread {
                // ─── PASS 2 · YellowLab Enhancement ─────────────────────────────────
                val tLlm = SystemClock.elapsedRealtime()
                DebugLog.i("PASS2", "━━━ PASS 2 · YellowLab Enhancement ━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                DebugLog.i("PASS2", "  in   : \"${withDict.take(300)}${if (withDict.length > 300) "…" else ""}\"")
                DebugLog.i("PASS2", "  inW  : ${pass1Words.size}  |  inC : ${withDict.length}")
                // A failed enhancement must not lose the dictation: fall back to the raw text.
                val (enhanced, llmBackend) = runCatching {
                    when {
                        AiCoreEnhancer.isReady() -> AiCoreEnhancer.enhance(withDict) to "aicore"
                        else -> LlmEnhancer.enhance(withDict) to LlmEnhancer.activeBackend
                    }
                }.getOrElse { t ->
                    DebugLog.e("PASS2", "enhancement failed — using the raw transcript", t)
                    withDict to ""
                }
                val llmMs = SystemClock.elapsedRealtime() - tLlm
                val pass2Words = enhanced.trim().split(Regex("\\s+")).filter { it.isNotBlank() }
                DebugLog.i("PASS2", "  out  : \"${enhanced.take(300)}${if (enhanced.length > 300) "…" else ""}\"")
                DebugLog.i("PASS2", "  outW : ${pass2Words.size}  |  outC : ${enhanced.length}")
                DebugLog.i("PASS2", "  ΔW   : ${pass2Words.size - pass1Words.size}  |  ΔC : ${enhanced.length - withDict.length}")
                DebugLog.i("PASS2", "  llmMs: $llmMs ms  backend: $llmBackend")
                DebugLog.i("PASS2", "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                // ────────────────────────────────────────────────────────────────────
                val modelName = LlmModelManager(this@VoiceOverlayService).getActiveConfig()?.displayName ?: ""
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
        val session = runCatching {
            historyManager.saveSession(rawText, finalText, elapsedMs, llmModel, llmMs, sttBackend, llmBackend)
        }.getOrElse { e ->
            DebugLog.e("Service", "history save failed — continuing without persisting", e)
            val words = finalText.trim().split(Regex("\\s+")).filter { it.isNotBlank() }
            DictationSession(0, System.currentTimeMillis(), finalText, rawText,
                words.size, elapsedMs, 0f, llmModel, llmMs, sttBackend, llmBackend)
        }

        DebugLog.i("Overlay", "inject node=${lastFocusedNode != null} final=\"${finalText.take(60)}\"")
        val injected = TextInjector.inject(lastFocusedNode, finalText)
        val wasEnhanced = rawText != finalText

        carLastTextFlow.value = finalText
        releaseHolds()
        pill.finish(PillState.Done(session.wordCount, session.wpm.toInt(), wasEnhanced, copied = !injected))

        val prefs = getSharedPreferences(PREF_FILE, android.content.Context.MODE_PRIVATE)
        if (prefs.getBoolean(KEY_CLIPBOARD_NOTIFY, true)) {
            fireTranscriptNotification(finalText)
        }

        // Copied = the field did not take it (no focus, or the paste failed); the toast says so.
        val enhancedSuffix = if (wasEnhanced) getString(R.string.models_overlay_enhanced_suffix) else ""
        val msg = when {
            !injected && session.wordCount > 0 ->
                getString(R.string.models_overlay_copied_stats, session.wordCount, "%.0f".format(session.wpm), enhancedSuffix)
            !injected ->
                getString(R.string.models_overlay_copied)
            session.wordCount > 0 ->
                getString(R.string.models_overlay_stats, session.wordCount, "%.0f".format(session.wpm), enhancedSuffix)
            else -> null
        }
        msg?.let { Toast.makeText(this, it, Toast.LENGTH_SHORT).show() }
    }

    /** The idle pill: where it sits and at what width. Keeps position, drops the screen/CPU holds. */
    private fun applyIdleWindow() {
        runCatching { windowManager.updateViewLayout(overlayView, params) }
    }

    /** Screen-on is only held during active recording — always release it, and the wake lock, when done. */
    private fun releaseHolds() {
        params.flags = params.flags and WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON.inv()
        runCatching { windowManager.updateViewLayout(overlayView, params) }
        if (wakeLock?.isHeld == true) {
            wakeLock?.release()
            DebugLog.i("Overlay", "wake lock released")
        }
    }

    private fun setIdleState() {
        releaseHolds()
        pill.reset()
    }

    fun isScreenOff(): Boolean = screenOff

    fun showOverlay() {
        overlayView.visibility = View.VISIBLE
    }

    fun hideOverlay() {
        if (pill.state.value.busy) {
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
        } else if (pill.state.value === PillState.Idle) {
            params.flags = params.flags and WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON.inv()
        }
        runCatching { windowManager.updateViewLayout(overlayView, params) }
    }

    fun reloadSpeechEngine() {
        val wasRecording = isListening
        if (wasRecording) stopRecording()
        speechEngine?.destroy()
        speechEngine = SpeechEngineFactory.create(this)
    }

    private fun fireTranscriptNotification(text: String) {
        val preview = if (text.length > 80) text.take(80) + "…" else text
        val notif = NotificationCompat.Builder(this, CHANNEL_TRANSCRIPT_ID)
            .setContentTitle(getString(R.string.models_overlay_copied))
            .setContentText(preview)
            .setStyle(NotificationCompat.BigTextStyle().bigText(text))
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setAutoCancel(true)
            .build()
        getSystemService(NotificationManager::class.java).notify(NOTIF_TRANSCRIPT_ID, notif)
    }

    private fun postRestoreNotification() {
        val pi = PendingIntent.getActivity(
            this, 0,
            Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
            PendingIntent.FLAG_IMMUTABLE
        )
        val nm = getSystemService(NotificationManager::class.java)
        val notif = NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle(getString(R.string.models_overlay_stopped_title))
            .setContentText(getString(R.string.models_overlay_stopped_text))
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setContentIntent(pi)
            .setAutoCancel(true)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .build()
        nm.notify(NOTIF_RESTORE_ID, notif)
    }

    private fun buildNotification(): Notification {
        val settingsPi = PendingIntent.getService(
            this, 0,
            Intent(this, VoiceOverlayService::class.java).setAction(ACTION_OPEN_SETTINGS),
            PendingIntent.FLAG_IMMUTABLE
        )
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle(getString(R.string.app_name))
            .setContentText(getString(R.string.models_overlay_ongoing_text))
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setOngoing(true)
            .setSilent(true)
            .addAction(android.R.drawable.ic_menu_preferences, getString(R.string.models_overlay_action_settings), settingsPi)
            .build()
    }

    private fun createNotificationChannel() {
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(
            NotificationChannel(CHANNEL_ID, getString(R.string.notif_channel_name), NotificationManager.IMPORTANCE_LOW)
                .also { it.description = getString(R.string.models_overlay_channel_desc) }
        )
        nm.createNotificationChannel(
            NotificationChannel(CHANNEL_TRANSCRIPT_ID, getString(R.string.models_overlay_channel_transcript), NotificationManager.IMPORTANCE_DEFAULT)
                .also { it.description = getString(R.string.models_overlay_channel_transcript_desc) }
        )
    }
}
