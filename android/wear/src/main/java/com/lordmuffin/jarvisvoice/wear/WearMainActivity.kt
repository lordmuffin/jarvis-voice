package com.lordmuffin.jarvisvoice.wear

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.view.View
import android.widget.Button
import android.widget.TextView
import android.widget.Toast
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import androidx.core.view.isVisible
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.lordmuffin.jarvisvoice.wear.sync.SyncScheduler
import com.lordmuffin.jarvisvoice.wearcommon.sync.JsonQueueStore
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

private const val RC_PERMISSIONS = 1001

/** One tap to record, one tap to stop. The service does the work; this screen only reflects it. */
class WearMainActivity : WearBaseActivity() {

    private lateinit var btnRecord: Button
    private lateinit var tvTimer: TextView
    private lateinit var tvResponse: TextView
    private lateinit var tvSyncStatus: TextView
    private lateinit var tvGlyph: TextView
    private lateinit var root: View
    private lateinit var graph: WearGraph
    private val pendingToggle = PendingToggle()

    private val handler = Handler(Looper.getMainLooper())
    private val tick = object : Runnable {
        override fun run() {
            RecorderState.active.value?.let { tvTimer.text = RecorderState.format(RecorderState.elapsedMs(SystemClock.elapsedRealtime(), it)) }
            handler.postDelayed(this, 250)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_wear_main)
        graph = WearGraph.get(this)

        btnRecord = findViewById(R.id.btn_record)
        tvTimer = findViewById(R.id.tv_timer)
        tvResponse = findViewById(R.id.tv_response)
        tvSyncStatus = findViewById(R.id.tv_sync_status)
        tvGlyph = findViewById(R.id.tv_state_glyph)
        root = findViewById(R.id.root)

        btnRecord.setOnClickListener { onRecordToggle() }
        findViewById<TextView>(R.id.tv_settings).setOnClickListener {
            startActivity(Intent(this, WearSettingsActivity::class.java))
        }

        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                launch { RecorderState.active.collectLatest { renderRecording(it != null) } }
                launch { graph.store.version.collectLatest { renderQueue() } }
                launch { RecorderState.failure.collectLatest { renderQueue() } }
            }
        }
        SyncScheduler.kick(this)   // opening the app is a good moment to catch up

        // A launch from recents replays the original intent too, with no saved state.
        val fromHistory = intent.flags and Intent.FLAG_ACTIVITY_LAUNCHED_FROM_HISTORY != 0
        pendingToggle.fromIntent(intent.getBooleanExtra(EXTRA_TOGGLE, false), restored = savedInstanceState != null || fromHistory)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        pendingToggle.fromIntent(intent.getBooleanExtra(EXTRA_TOGGLE, false), restored = false)
    }

    override fun onResume() {
        super.onResume()
        if (pendingToggle.take()) onRecordToggle()
    }

    private fun onRecordToggle() {
        if (RecorderState.active.value != null) {
            RecordingService.stop(this)
            return
        }
        if (!hasMic()) {
            val wanted = buildList {
                add(Manifest.permission.RECORD_AUDIO)
                if (Build.VERSION.SDK_INT >= 33) add(Manifest.permission.POST_NOTIFICATIONS)
            }
            ActivityCompat.requestPermissions(this, wanted.toTypedArray(), RC_PERMISSIONS)
            return
        }
        RecordingService.start(this)
    }

    private fun hasMic() =
        ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED

    private fun renderRecording(recording: Boolean) {
        handler.removeCallbacks(tick)
        if (recording) {
            tvTimer.isVisible = true
            tvGlyph.isVisible = false
            root.setBackgroundResource(R.drawable.rec_ring)
            btnRecord.setBackgroundResource(R.drawable.btn_stop_bg)
            btnRecord.setTextColor(ContextCompat.getColor(this, R.color.inst_text))
            btnRecord.contentDescription = getString(R.string.wear_stop_capture)
            btnRecord.text = "⏹"
            tvResponse.setText(R.string.wear_state_recording)
            tvResponse.setCompoundDrawablesWithIntrinsicBounds(R.drawable.rec_dot, 0, 0, 0)
            tvResponse.compoundDrawablePadding = 6
            tvSyncStatus.text = ""
            handler.post(tick)
        } else {
            tvTimer.isVisible = false
            root.setBackgroundColor(ContextCompat.getColor(this, R.color.inst_bg))
            btnRecord.setBackgroundResource(R.drawable.btn_record_bg)
            btnRecord.setTextColor(ContextCompat.getColor(this, R.color.inst_on_accent))
            btnRecord.contentDescription = getString(R.string.wear_start_capture)
            btnRecord.text = "🎙"
            tvResponse.setCompoundDrawablesWithIntrinsicBounds(0, 0, 0, 0)
            lifecycleScope.launch { renderQueue() }
        }
    }

    private suspend fun renderQueue() {
        if (RecorderState.active.value != null) return   // recording owns the screen
        val ui = withContext(Dispatchers.IO) { WearUiState.forQueue(graph.store.all(), RecorderState.failure.value) }
        val tone = when (ui.tone) {
            WearUiState.Tone.SUCCESS -> R.color.inst_success
            WearUiState.Tone.WARNING -> R.color.inst_warning
            else -> R.color.inst_text
        }
        tvGlyph.isVisible = ui.glyph != WearUiState.Glyph.NONE
        tvGlyph.text = when (ui.glyph) {
            WearUiState.Glyph.SQUARE -> "■"
            WearUiState.Glyph.CHECK -> "✓"
            WearUiState.Glyph.FLAG -> "⚑"
            WearUiState.Glyph.NONE -> ""
        }
        tvGlyph.setTextColor(ContextCompat.getColor(this, tone))
        tvResponse.setText(ui.labelRes)
        tvSyncStatus.text = buildList {
            if (ui.waiting > 0) add(getString(R.string.wear_meta_waiting, ui.waiting))
            ui.metaRes?.let { add(getString(it)) }
        }.joinToString(" · ")
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode != RC_PERMISSIONS) return
        // Not from here: the screen is not resumed yet, and the microphone service needs a visible app.
        if (hasMic()) {
            pendingToggle.request()
            if (lifecycle.currentState.isAtLeast(Lifecycle.State.RESUMED) && pendingToggle.take()) onRecordToggle()
        } else Toast.makeText(this, getString(R.string.wear_mic_required), Toast.LENGTH_SHORT).show()
    }

    override fun onStop() {
        // The wearer has seen it. One that happens while the screen is away waits here for them.
        RecorderState.clearFailure()
        super.onStop()
    }

    override fun onDestroy() {
        handler.removeCallbacks(tick)
        super.onDestroy()
    }

    companion object {
        /** Start or stop once the screen is visible: the tile, the complication and a mapped button send this. */
        const val EXTRA_TOGGLE = "toggle"
    }
}
