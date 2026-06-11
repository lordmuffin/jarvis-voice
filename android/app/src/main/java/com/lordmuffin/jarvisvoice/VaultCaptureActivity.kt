package com.lordmuffin.jarvisvoice

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.View
import android.view.WindowManager
import android.widget.*
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import com.lordmuffin.jarvisvoice.speech.SpeechEngine
import com.lordmuffin.jarvisvoice.speech.SpeechEngineFactory
import com.lordmuffin.jarvisvoice.ui.AudioWaveformView

class VaultCaptureActivity : AppCompatActivity() {

    private enum class State { IDLE, RECORDING, CONFIRMING }

    private lateinit var scrollView:     ScrollView
    private lateinit var idleGroup:      View
    private lateinit var recordingGroup: View
    private lateinit var confirmGroup:   View
    private lateinit var waveform:       AudioWaveformView
    private lateinit var tvInterim:      TextView
    private lateinit var tvFolderHint:   TextView
    private lateinit var etTranscript:   EditText
    private lateinit var tvStatus:       TextView
    private lateinit var btnSave:        Button
    private lateinit var fabStop:        Button

    private var engine: SpeechEngine? = null
    private val mainHandler = Handler(Looper.getMainLooper())
    private var currentState = State.IDLE

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_vault_capture)
        supportActionBar?.title = "Voice to Vault"
        BottomNav.wire(this, BottomNav.Tab.RECORD)

        scrollView    = findViewById(R.id.vault_scroll)
        idleGroup     = findViewById(R.id.group_idle)
        recordingGroup = findViewById(R.id.group_recording)
        confirmGroup  = findViewById(R.id.group_confirm)
        waveform      = findViewById(R.id.vault_waveform)
        tvInterim     = findViewById(R.id.tv_interim)
        tvFolderHint  = findViewById(R.id.tv_folder_hint)
        etTranscript  = findViewById(R.id.et_transcript)
        tvStatus      = findViewById(R.id.tv_status)
        btnSave       = findViewById(R.id.btn_save)
        fabStop       = findViewById(R.id.fab_stop)

        waveform.barColor = getColor(R.color.jv_accent)
        fabStop.setOnClickListener { onStop(it) }
        applyState(State.IDLE)
    }

    override fun onResume() {
        super.onResume()
        refreshFolderHint()
        // Auto-start recording when the screen opens, if conditions are met
        if (currentState == State.IDLE) attemptAutoStart()
    }

    override fun onDestroy() {
        super.onDestroy()
        engine?.destroy()
        engine = null
        window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
    }

    // ── UI state ─────────────────────────────────────────────────────────────

    private fun applyState(s: State) {
        currentState = s
        idleGroup.visibility      = if (s == State.IDLE)       View.VISIBLE else View.GONE
        recordingGroup.visibility = if (s == State.RECORDING)  View.VISIBLE else View.GONE
        confirmGroup.visibility   = if (s == State.CONFIRMING) View.VISIBLE else View.GONE
        fabStop.visibility        = if (s == State.RECORDING)  View.VISIBLE else View.GONE

        if (s == State.RECORDING) {
            window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        } else {
            window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        }
    }

    private fun refreshFolderHint() {
        if (VaultNoteWriter.isConfigured(this)) {
            tvFolderHint.text = "→ ${VaultNoteWriter.displayPath(this)}"
            tvFolderHint.setTextColor(getColor(R.color.jv_success))
        } else {
            tvFolderHint.text = "No folder selected — tap below to pick one"
            tvFolderHint.setTextColor(getColor(R.color.jv_warning))
        }
    }

    // ── Auto-start ────────────────────────────────────────────────────────────

    private fun attemptAutoStart() {
        if (!VaultNoteWriter.isConfigured(this)) return  // stay on IDLE so user sees folder prompt
        when {
            ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
                    != PackageManager.PERMISSION_GRANTED ->
                requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), REQ_MIC)
            else -> startCapture()
        }
    }

    // ── Button handlers ───────────────────────────────────────────────────────

    fun onPickFolder(v: View) {
        val intent = Intent(Intent.ACTION_OPEN_DOCUMENT_TREE).apply {
            addFlags(
                Intent.FLAG_GRANT_READ_URI_PERMISSION or
                Intent.FLAG_GRANT_WRITE_URI_PERMISSION or
                Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION
            )
        }
        @Suppress("DEPRECATION")
        startActivityForResult(intent, REQ_FOLDER)
    }

    fun onMicTap(v: View) {
        if (!VaultNoteWriter.isConfigured(this)) {
            showStatus("Pick a vault folder first (tap 'Change Folder' below).", R.color.jv_warning)
            return
        }
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
                != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), REQ_MIC)
            return
        }
        startCapture()
    }

    fun onStop(v: View) {
        engine?.stopListening()
    }

    fun onSave(v: View) {
        val transcript = etTranscript.text.toString().trim()
        if (transcript.isEmpty()) {
            showStatus("Nothing to save.", R.color.jv_warning)
            return
        }

        btnSave.isEnabled = false
        showStatus("Saving…", R.color.jv_text2)

        Thread {
            val result = VaultNoteWriter.appendToday(this, transcript)
            mainHandler.post {
                if (result.isSuccess) {
                    showStatus("Saved to today's note.", R.color.jv_success)
                    mainHandler.postDelayed({
                        etTranscript.setText("")
                        tvStatus.visibility = View.GONE
                        btnSave.isEnabled = true
                        applyState(State.IDLE)
                    }, 1_200)
                } else {
                    showStatus("Failed: ${result.exceptionOrNull()?.message}", R.color.jv_error)
                    btnSave.isEnabled = true
                }
            }
        }.start()
    }

    fun onDiscard(v: View) {
        etTranscript.setText("")
        tvStatus.visibility = View.GONE
        applyState(State.IDLE)
    }

    @Deprecated("Using deprecated API for compatibility with minSdk 24")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        @Suppress("DEPRECATION")
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == REQ_FOLDER && resultCode == RESULT_OK) {
            data?.data?.let { uri ->
                VaultNoteWriter.saveFolderUri(this, uri)
                refreshFolderHint()
                showStatus("Vault folder set.", R.color.jv_success)
            }
        }
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == REQ_MIC && grantResults.isNotEmpty() && grantResults[0] == PackageManager.PERMISSION_GRANTED)
            startCapture()
    }

    // ── Recording ─────────────────────────────────────────────────────────────

    private fun startCapture() {
        VoiceOverlayService.instance?.cancelActiveRecording()

        if (engine == null) engine = SpeechEngineFactory.create(this)

        tvInterim.text = ""
        tvStatus.visibility = View.GONE
        waveform.startAnimation()
        applyState(State.RECORDING)

        engine?.startListening(
            holdMode  = false,
            onPartial = { partial ->
                mainHandler.post {
                    tvInterim.text = partial
                    scrollToBottom()
                }
            },
            onFinal   = { final ->
                mainHandler.post {
                    waveform.stopAnimation()
                    val text = final.trim()
                    etTranscript.setText(text)
                    etTranscript.setSelection(text.length)
                    applyState(if (text.isEmpty()) State.IDLE else State.CONFIRMING)
                    if (text.isNotEmpty()) scrollToBottom()
                }
            },
            onError = { _ ->
                mainHandler.post {
                    waveform.stopAnimation()
                    showStatus("Recording failed. Try again.", R.color.jv_error)
                    applyState(State.IDLE)
                }
            },
        )
    }

    private fun scrollToBottom() {
        scrollView.post { scrollView.fullScroll(View.FOCUS_DOWN) }
    }

    private fun showStatus(msg: String, colorRes: Int) {
        tvStatus.text = msg
        tvStatus.setTextColor(getColor(colorRes))
        tvStatus.visibility = View.VISIBLE
    }

    companion object {
        private const val REQ_MIC    = 42
        private const val REQ_FOLDER = 43
    }
}
