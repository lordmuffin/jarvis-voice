package com.lordmuffin.jarvisvoice.wear

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.View
import android.widget.Button
import android.widget.TextView
import android.widget.Toast
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import androidx.core.view.isVisible
import androidx.lifecycle.lifecycleScope
import androidx.work.Constraints
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import com.lordmuffin.jarvisvoice.wear.db.RecordingStore
import com.lordmuffin.jarvisvoice.wear.db.SyncStatus
import com.lordmuffin.jarvisvoice.wear.sync.UploadWorker
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.util.Locale

private const val RC_AUDIO = 1001

class WearMainActivity : WearBaseActivity() {

    private lateinit var btnRecord: Button
    private lateinit var tvTimer: TextView
    private lateinit var tvResponse: TextView
    private lateinit var tvSyncStatus: TextView
    private lateinit var tvGlyph: TextView
    private lateinit var root: View

    private var recorder: AudioRecorder? = null

    private val handler = Handler(Looper.getMainLooper())
    private var elapsedSeconds = 0
    private var lastDuration: String? = null
    private val timerRunnable = object : Runnable {
        override fun run() {
            elapsedSeconds++
            val m = elapsedSeconds / 60
            val s = elapsedSeconds % 60
            tvTimer.text = String.format(Locale.US, "%d:%02d", m, s)
            handler.postDelayed(this, 1000)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_wear_main)

        btnRecord    = findViewById(R.id.btn_record)
        tvTimer      = findViewById(R.id.tv_timer)
        tvResponse   = findViewById(R.id.tv_response)
        tvSyncStatus = findViewById(R.id.tv_sync_status)
        tvGlyph      = findViewById(R.id.tv_state_glyph)
        root         = findViewById(R.id.root)

        recorder = AudioRecorder(filesDir)

        btnRecord.setOnClickListener { onRecordToggle() }
        findViewById<TextView>(R.id.tv_settings).setOnClickListener {
            startActivity(Intent(this, WearSettingsActivity::class.java))
        }

        observeLatestRecording()
    }

    private fun onRecordToggle() {
        val rec = recorder ?: return
        if (rec.isActive) {
            stopCapture(rec)
        } else {
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
                != PackageManager.PERMISSION_GRANTED) {
                ActivityCompat.requestPermissions(this, arrayOf(Manifest.permission.RECORD_AUDIO), RC_AUDIO)
                return
            }
            startCapture(rec)
        }
    }

    private fun startCapture(rec: AudioRecorder) {
        rec.startRecording()
        elapsedSeconds = 0
        tvTimer.text = "0:00"
        tvTimer.visibility = View.VISIBLE
        tvGlyph.visibility = View.GONE
        handler.post(timerRunnable)
        btnRecord.setText(R.string.wear_stop_glyph)
        tvResponse.setText(R.string.wear_recording)
        root.setBackgroundResource(R.drawable.rec_ring)
        btnRecord.setBackgroundResource(R.drawable.btn_stop_bg)
        btnRecord.setTextColor(ContextCompat.getColor(this, R.color.inst_text))
        btnRecord.contentDescription = getString(R.string.wear_stop_capture)
        btnRecord.text = "⏹"
        tvResponse.setText(R.string.wear_state_recording)
        tvResponse.setTextColor(ContextCompat.getColor(this, R.color.inst_text))
        tvResponse.setCompoundDrawablesWithIntrinsicBounds(R.drawable.rec_dot, 0, 0, 0)
        tvResponse.compoundDrawablePadding = 6
        tvSyncStatus.text = ""
    }

    private fun stopCapture(rec: AudioRecorder) {
        val file = rec.stopRecording()
        handler.removeCallbacks(timerRunnable)
        tvTimer.visibility = View.INVISIBLE
        btnRecord.setText(R.string.wear_mic_glyph)
        tvTimer.visibility = View.GONE
        root.setBackgroundColor(ContextCompat.getColor(this, R.color.inst_bg))
        btnRecord.setBackgroundResource(R.drawable.btn_record_bg)
        btnRecord.setTextColor(ContextCompat.getColor(this, R.color.inst_on_accent))
        btnRecord.contentDescription = getString(R.string.wear_start_capture)
        btnRecord.text = "🎙"
        tvResponse.setCompoundDrawablesWithIntrinsicBounds(0, 0, 0, 0)

        if (file == null) return

        val duration = String.format(Locale.US, "%d:%02d", elapsedSeconds / 60, elapsedSeconds % 60)
        lifecycleScope.launch(Dispatchers.IO) {
            RecordingStore.get(applicationContext).insert(file.absolutePath, System.currentTimeMillis())
            enqueueUpload()
        }
        tvResponse.setText(R.string.wear_saved_will_sync)
        tvSyncStatus.setText(R.string.wear_pending)
        // The latestFlow collector renders the Saved state; remember the length for its meta line.
        lastDuration = duration
    }

    private fun enqueueUpload() {
        val work = OneTimeWorkRequestBuilder<UploadWorker>()
            .setConstraints(
                Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()
            )
            .addTag(UploadWorker.WORK_TAG)
            .build()
        WorkManager.getInstance(applicationContext)
            .enqueueUniqueWork(UploadWorker.WORK_TAG, ExistingWorkPolicy.APPEND_OR_REPLACE, work)
    }

    private fun observeLatestRecording() {
        lifecycleScope.launch {
            RecordingStore.get(this@WearMainActivity).latestFlow.collect { rec ->
                if (rec == null) {
                    tvResponse.setText(R.string.wear_tap_to_capture)
                    tvSyncStatus.text = ""
                    return@collect
                }
                when (rec.status) {
                    SyncStatus.PENDING -> {
                        tvResponse.setText(R.string.wear_saved_will_sync)
                        tvSyncStatus.setText(R.string.wear_pending)
                    }
                    SyncStatus.SYNCED -> {
                        tvResponse.text = rec.response?.takeIf { it.isNotBlank() } ?: getString(R.string.wear_captured)
                        tvSyncStatus.setText(R.string.wear_synced)
                    }
                    SyncStatus.FAILED -> {
                        tvResponse.setText(R.string.wear_sync_failed)
                        tvSyncStatus.setText(R.string.wear_failed)
                    }
                if (recorder?.isActive == true) return@collect // Recording owns the screen.
                val waiting = withContext(Dispatchers.IO) {
                    RecordingStore.get(applicationContext).getPending().size
                }
                render(WearUiState.forLatest(rec?.status, waiting, rec?.response))
            }
        }
    }

    private fun render(ui: WearUiState) {
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
        tvResponse.text = ui.response ?: getString(ui.labelRes)
        tvResponse.setTextColor(ContextCompat.getColor(this, R.color.inst_text))
        val meta = buildList {
            if (ui.glyph == WearUiState.Glyph.SQUARE) {
                lastDuration?.let { add(it) }
                add(getString(R.string.wear_meta_waiting, ui.waiting))
            }
            ui.metaRes?.let { add(getString(it)) }
        }
        tvSyncStatus.text = meta.joinToString(" · ")
    }

    override fun onRequestPermissionsResult(
        requestCode: Int, permissions: Array<String>, grantResults: IntArray
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == RC_AUDIO && grantResults.firstOrNull() == PackageManager.PERMISSION_GRANTED) {
            startCapture(recorder ?: return)
        } else {
            Toast.makeText(this, R.string.wear_mic_permission, Toast.LENGTH_SHORT).show()
            Toast.makeText(this, getString(R.string.wear_mic_required), Toast.LENGTH_SHORT).show()
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        handler.removeCallbacks(timerRunnable)
    }
}
