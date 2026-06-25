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
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
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
import java.util.Locale

private const val RC_AUDIO = 1001

class WearMainActivity : AppCompatActivity() {

    private lateinit var btnRecord: Button
    private lateinit var tvTimer: TextView
    private lateinit var tvResponse: TextView
    private lateinit var tvSyncStatus: TextView

    private var recorder: AudioRecorder? = null

    private val handler = Handler(Looper.getMainLooper())
    private var elapsedSeconds = 0
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
        tvTimer.visibility = View.VISIBLE
        handler.post(timerRunnable)
        btnRecord.text = "⏹"
        tvResponse.text = "Recording…"
        tvSyncStatus.text = ""
    }

    private fun stopCapture(rec: AudioRecorder) {
        val file = rec.stopRecording()
        handler.removeCallbacks(timerRunnable)
        tvTimer.visibility = View.INVISIBLE
        btnRecord.text = "🎙"

        if (file == null) return

        lifecycleScope.launch(Dispatchers.IO) {
            RecordingStore.get(applicationContext).insert(file.absolutePath, System.currentTimeMillis())
            enqueueUpload()
        }
        tvResponse.text = "Saved — will sync on WiFi"
        tvSyncStatus.text = "⏳ pending"
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
                    tvResponse.text = "Tap to capture"
                    tvSyncStatus.text = ""
                    return@collect
                }
                when (rec.status) {
                    SyncStatus.PENDING -> {
                        tvResponse.text = "Saved — will sync on WiFi"
                        tvSyncStatus.text = "⏳ pending"
                    }
                    SyncStatus.SYNCED -> {
                        tvResponse.text = rec.response?.takeIf { it.isNotBlank() } ?: "✓ Captured"
                        tvSyncStatus.text = "✓ synced"
                    }
                    SyncStatus.FAILED -> {
                        tvResponse.text = "⚠ Sync failed — will retry"
                        tvSyncStatus.text = "✗ failed"
                    }
                }
            }
        }
    }

    override fun onRequestPermissionsResult(
        requestCode: Int, permissions: Array<String>, grantResults: IntArray
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == RC_AUDIO && grantResults.firstOrNull() == PackageManager.PERMISSION_GRANTED) {
            startCapture(recorder ?: return)
        } else {
            Toast.makeText(this, "Microphone permission required", Toast.LENGTH_SHORT).show()
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        handler.removeCallbacks(timerRunnable)
    }
}
