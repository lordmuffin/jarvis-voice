package dev.apj.jarvisvoice

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Intent
import android.graphics.PixelFormat
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.view.Gravity
import android.view.LayoutInflater
import android.view.MotionEvent
import android.view.View
import android.view.ViewConfiguration
import android.view.WindowManager
import android.view.accessibility.AccessibilityNodeInfo
import android.widget.TextView
import android.widget.Toast
import androidx.core.app.NotificationCompat
import dev.apj.jarvisvoice.speech.AndroidSpeechEngine
import dev.apj.jarvisvoice.speech.SpeechEngine
import dev.apj.jarvisvoice.ui.AudioWaveformView

enum class OverlayState { IDLE, RECORDING, DONE }

class VoiceOverlayService : Service() {

    companion object {
        const val CHANNEL_ID = "jarvis_voice_overlay"
        const val NOTIF_ID = 1
        var lastFocusedNode: AccessibilityNodeInfo? = null
        var instance: VoiceOverlayService? = null
    }

    private lateinit var windowManager: WindowManager
    private lateinit var overlayView: View
    private lateinit var waveformView: AudioWaveformView
    private lateinit var transcriptText: TextView
    private lateinit var micIcon: View
    private var speechEngine: SpeechEngine? = null
    private var state = OverlayState.IDLE

    // Drag state
    private var initialX = 0
    private var initialY = 0
    private var initialTouchX = 0f
    private var initialTouchY = 0f
    private var isDragging = false
    private var longPressRunnable: Runnable? = null
    private val longPressHandler = Handler(Looper.getMainLooper())

    private lateinit var params: WindowManager.LayoutParams

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        instance = this
        createNotificationChannel()
        startForeground(NOTIF_ID, buildNotification())
        setupOverlay()
        speechEngine = AndroidSpeechEngine(this)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int = START_STICKY

    override fun onDestroy() {
        super.onDestroy()
        instance = null
        speechEngine?.destroy()
        if (::overlayView.isInitialized) {
            runCatching { windowManager.removeView(overlayView) }
        }
    }

    private fun setupOverlay() {
        windowManager = getSystemService(WINDOW_SERVICE) as WindowManager

        val inflater = LayoutInflater.from(this)
        overlayView = inflater.inflate(R.layout.overlay_pill, null)
        waveformView = overlayView.findViewById(R.id.waveform)
        transcriptText = overlayView.findViewById(R.id.transcript)
        micIcon = overlayView.findViewById(R.id.mic_icon)

        val displayMetrics = resources.displayMetrics
        val widthPx = (220 * displayMetrics.density).toInt()
        val heightPx = (56 * displayMetrics.density).toInt()

        params = WindowManager.LayoutParams(
            widthPx,
            heightPx,
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                    WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
            PixelFormat.TRANSLUCENT
        ).apply {
            gravity = Gravity.BOTTOM or Gravity.CENTER_HORIZONTAL
            y = (64 * displayMetrics.density).toInt()
        }

        overlayView.setOnTouchListener(::handleTouch)
        windowManager.addView(overlayView, params)
        setIdleState()
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
                    isDragging = false
                    startHoldToRecord()
                }
                longPressHandler.postDelayed(
                    longPressRunnable!!,
                    ViewConfiguration.getLongPressTimeout().toLong()
                )
            }
            MotionEvent.ACTION_MOVE -> {
                val dx = (event.rawX - initialTouchX).toInt()
                val dy = (event.rawY - initialTouchY).toInt()
                if (!isDragging && (Math.abs(dx) > 10 || Math.abs(dy) > 10)) {
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
                if (!isDragging) {
                    when (state) {
                        OverlayState.IDLE -> startRecording()
                        OverlayState.RECORDING -> stopRecording()
                        OverlayState.DONE -> setIdleState()
                    }
                } else if (state == OverlayState.RECORDING) {
                    stopRecording()
                }
            }
        }
        return true
    }

    private fun startHoldToRecord() {
        if (state == OverlayState.IDLE) startRecording()
    }

    fun startRecording() {
        state = OverlayState.RECORDING
        micIcon.visibility = View.GONE
        waveformView.visibility = View.VISIBLE
        waveformView.startAnimation()
        transcriptText.visibility = View.VISIBLE
        transcriptText.text = ""

        speechEngine?.startListening(
            onPartial = { partial ->
                Handler(Looper.getMainLooper()).post {
                    transcriptText.text = partial
                }
            },
            onFinal = { final ->
                Handler(Looper.getMainLooper()).post {
                    handleFinalTranscript(final)
                }
            },
            onError = { _ ->
                Handler(Looper.getMainLooper()).post {
                    setIdleState()
                }
            }
        )
    }

    private fun stopRecording() {
        speechEngine?.stopListening()
    }

    private fun handleFinalTranscript(text: String) {
        if (text.isBlank()) {
            setIdleState()
            return
        }
        state = OverlayState.DONE
        waveformView.stopAnimation()

        val node = lastFocusedNode
        if (node != null) {
            val injected = TextInjector.inject(node, text)
            if (!injected) {
                showCopyButton(text)
                return
            }
        } else {
            Toast.makeText(this, "No text field focused", Toast.LENGTH_SHORT).show()
        }

        overlayView.setBackgroundColor(0xFF00B4D8.toInt())
        Handler(Looper.getMainLooper()).postDelayed({
            setIdleState()
        }, 500)
    }

    private fun showCopyButton(text: String) {
        transcriptText.text = "Tap to copy: $text"
        transcriptText.setOnClickListener {
            val clipboard = getSystemService(CLIPBOARD_SERVICE) as ClipboardManager
            clipboard.setPrimaryClip(ClipData.newPlainText("transcript", text))
            Toast.makeText(this, "Copied", Toast.LENGTH_SHORT).show()
            transcriptText.setOnClickListener(null)
            setIdleState()
        }
    }

    private fun setIdleState() {
        state = OverlayState.IDLE
        micIcon.visibility = View.VISIBLE
        waveformView.visibility = View.GONE
        waveformView.stopAnimation()
        transcriptText.visibility = View.GONE
        transcriptText.text = ""
        transcriptText.setOnClickListener(null)
        overlayView.setBackgroundResource(R.drawable.pill_background)
    }

    fun showOverlay() {
        overlayView.visibility = View.VISIBLE
    }

    fun hideOverlay() {
        if (state == OverlayState.RECORDING) stopRecording()
        overlayView.visibility = View.GONE
    }

    private fun buildNotification(): Notification {
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("Jarvis Voice")
            .setContentText("Dictation overlay active")
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setOngoing(true)
            .setSilent(true)
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
