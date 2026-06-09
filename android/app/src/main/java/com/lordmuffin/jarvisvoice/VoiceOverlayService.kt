package com.lordmuffin.jarvisvoice

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.graphics.Color
import android.graphics.PixelFormat
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.SystemClock
import android.view.Gravity
import android.view.LayoutInflater
import android.view.MotionEvent
import android.view.View
import android.view.ViewConfiguration
import android.view.WindowManager
import android.view.accessibility.AccessibilityNodeInfo
import android.widget.ImageView
import android.widget.Toast
import androidx.core.app.NotificationCompat
import com.lordmuffin.jarvisvoice.speech.SpeechEngine
import com.lordmuffin.jarvisvoice.speech.SpeechEngineFactory
import com.lordmuffin.jarvisvoice.ui.AudioWaveformView

enum class OverlayState { IDLE, RECORDING, DONE }

class VoiceOverlayService : Service() {

    companion object {
        const val CHANNEL_ID         = "jarvis_voice_overlay"
        const val NOTIF_ID           = 1
        const val ACTION_OPEN_SETTINGS = "com.lordmuffin.jarvisvoice.OPEN_SETTINGS"
        var lastFocusedNode: AccessibilityNodeInfo? = null
        var instance: VoiceOverlayService? = null
    }

    private lateinit var windowManager: WindowManager
    private lateinit var overlayView: View
    private lateinit var waveformView: AudioWaveformView
    private lateinit var micIcon: ImageView
    private var speechEngine: SpeechEngine? = null
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
    private var longPressRunnable: Runnable? = null
    private val longPressHandler = Handler(Looper.getMainLooper())

    private lateinit var params: WindowManager.LayoutParams

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        instance       = this
        historyManager = DictationHistoryManager(this)
        dictManager    = CustomDictionaryManager(this)
        createNotificationChannel()
        startForeground(NOTIF_ID, buildNotification())
        setupOverlay()
        speechEngine = SpeechEngineFactory.create(this)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_OPEN_SETTINGS) {
            startActivity(
                Intent(this, SettingsActivity::class.java)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            )
        }
        return START_STICKY
    }

    override fun onDestroy() {
        super.onDestroy()
        instance = null
        speechEngine?.destroy()
        historyManager.close()
        if (::overlayView.isInitialized) {
            runCatching { windowManager.removeView(overlayView) }
        }
    }

    private fun setupOverlay() {
        windowManager = getSystemService(WINDOW_SERVICE) as WindowManager

        overlayView  = LayoutInflater.from(this).inflate(R.layout.overlay_pill, null)
        waveformView = overlayView.findViewById(R.id.waveform)
        micIcon      = overlayView.findViewById(R.id.mic_icon)

        val density = resources.displayMetrics.density
        val sizePx  = (56 * density).toInt()

        params = WindowManager.LayoutParams(
            sizePx,
            sizePx,
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                    WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
            PixelFormat.TRANSLUCENT
        ).apply {
            gravity = Gravity.BOTTOM or Gravity.END
            x = (16 * density).toInt()
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
                    params.x = initialX - dx
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
                        OverlayState.IDLE      -> startRecording(holdMode = false)
                        OverlayState.RECORDING -> stopRecording()
                        OverlayState.DONE      -> setIdleState()
                    }
                } else if (state == OverlayState.RECORDING) {
                    stopRecording()
                }
            }
        }
        return true
    }

    fun startRecording(holdMode: Boolean = false) {
        sessionStartMs = SystemClock.elapsedRealtime()
        state = OverlayState.RECORDING
        micIcon.visibility = View.GONE
        waveformView.barColor = Color.WHITE
        waveformView.visibility = View.VISIBLE
        waveformView.startAnimation()
        overlayView.setBackgroundColor(0xFFFF4444.toInt())

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
        speechEngine?.stopListening()
    }

    private fun handleFinalTranscript(text: String) {
        val elapsedMs = SystemClock.elapsedRealtime() - sessionStartMs
        if (text.isBlank()) { setIdleState(); return }

        val processed = dictManager.applyTo(text)
        val session   = historyManager.saveSession(processed, elapsedMs)

        state = OverlayState.DONE
        waveformView.stopAnimation()

        TextInjector.inject(lastFocusedNode, processed)

        // Metro: show WPM after every session
        val msg = when {
            lastFocusedNode == null && session.wordCount > 0 ->
                "Copied · ${session.wordCount} words · ${"%.0f".format(session.wpm)} wpm"
            lastFocusedNode == null ->
                "Copied to clipboard"
            session.wordCount > 0 ->
                "${session.wordCount} words · ${"%.0f".format(session.wpm)} wpm"
            else -> null
        }
        msg?.let { Toast.makeText(this, it, Toast.LENGTH_SHORT).show() }

        overlayView.setBackgroundColor(0xFF00B4D8.toInt())
        Handler(Looper.getMainLooper()).postDelayed({ setIdleState() }, 400)
    }

    private fun setIdleState() {
        state = OverlayState.IDLE
        micIcon.visibility = View.VISIBLE
        waveformView.visibility = View.GONE
        waveformView.stopAnimation()
        overlayView.setBackgroundResource(R.drawable.pill_background)
    }

    fun showOverlay() {
        overlayView.visibility = View.VISIBLE
    }

    fun hideOverlay() {
        if (state == OverlayState.RECORDING) stopRecording()
        overlayView.visibility = View.GONE
    }

    fun reloadSpeechEngine() {
        val wasRecording = state == OverlayState.RECORDING
        if (wasRecording) stopRecording()
        speechEngine?.destroy()
        speechEngine = SpeechEngineFactory.create(this)
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
        val channel = NotificationChannel(
            CHANNEL_ID,
            "Jarvis Voice Overlay",
            NotificationManager.IMPORTANCE_LOW
        )
        channel.description = "Persistent notification for dictation overlay"
        getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
    }
}
