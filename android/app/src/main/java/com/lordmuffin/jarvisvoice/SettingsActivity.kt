package com.lordmuffin.jarvisvoice

import android.content.Intent
import android.os.Bundle
import android.view.View
import android.widget.Button
import android.widget.ProgressBar
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.appcompat.widget.SwitchCompat
import com.lordmuffin.jarvisvoice.speech.ModelDownloader
import com.lordmuffin.jarvisvoice.speech.SherpaOnnxSpeechEngine
import com.lordmuffin.jarvisvoice.speech.SpeechEngineFactory

class SettingsActivity : AppCompatActivity() {

    private lateinit var offlineSwitch: SwitchCompat
    private lateinit var clipboardNotifySwitch: SwitchCompat
    private lateinit var tvModelStatus: TextView
    private lateinit var btnDownload: Button
    private lateinit var progressBar: ProgressBar
    private lateinit var tvDownloadStatus: TextView
    private lateinit var btnCancel: Button

    // Stats views
    private lateinit var tvLastWords: TextView
    private lateinit var tvLastWpm: TextView
    private lateinit var tvTotalWords: TextView
    private lateinit var tvAvgWpm: TextView

    private var downloader: ModelDownloader? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_settings)
        supportActionBar?.title = "Jarvis Voice Settings"

        offlineSwitch         = findViewById(R.id.switch_offline_stt)
        clipboardNotifySwitch = findViewById(R.id.switch_clipboard_notify)
        tvModelStatus         = findViewById(R.id.tv_model_status)
        btnDownload      = findViewById(R.id.btn_download_model)
        progressBar      = findViewById(R.id.progress_download)
        tvDownloadStatus = findViewById(R.id.tv_download_status)
        btnCancel        = findViewById(R.id.btn_cancel_download)

        tvLastWords  = findViewById(R.id.tv_stat_last_words)
        tvLastWpm    = findViewById(R.id.tv_stat_last_wpm)
        tvTotalWords = findViewById(R.id.tv_stat_total_words)
        tvAvgWpm     = findViewById(R.id.tv_stat_avg_wpm)

        offlineSwitch.isChecked = SpeechEngineFactory.isOfflineModeEnabled(this)
        offlineSwitch.setOnCheckedChangeListener { _, checked ->
            SpeechEngineFactory.setOfflineMode(this, checked)
            VoiceOverlayService.instance?.reloadSpeechEngine()
        }

        val prefs = getSharedPreferences(VoiceOverlayService.PREF_FILE, MODE_PRIVATE)
        clipboardNotifySwitch.isChecked = prefs.getBoolean(VoiceOverlayService.KEY_CLIPBOARD_NOTIFY, true)
        clipboardNotifySwitch.setOnCheckedChangeListener { _, checked ->
            prefs.edit().putBoolean(VoiceOverlayService.KEY_CLIPBOARD_NOTIFY, checked).apply()
        }

        btnDownload.setOnClickListener { startDownload() }
        btnCancel.setOnClickListener {
            downloader?.cancel()
            resetDownloadUi()
        }

        findViewById<Button>(R.id.btn_open_history).setOnClickListener {
            startActivity(Intent(this, HistoryActivity::class.java))
        }
        findViewById<Button>(R.id.btn_open_dictionary).setOnClickListener {
            startActivity(Intent(this, DictionaryActivity::class.java))
        }

        refreshModelStatus()
        refreshStats()
    }

    override fun onResume() {
        super.onResume()
        refreshStats()
    }

    private fun refreshStats() {
        val db = DictationHistoryManager(this)
        val last     = db.getLastSession()
        val lifetime = db.getLifetimeStats()
        db.close()

        tvLastWords.text  = last?.wordCount?.toString() ?: "—"
        tvLastWpm.text    = last?.let { "%.0f".format(it.wpm) } ?: "—"
        tvTotalWords.text = lifetime.totalWords.toString()
        tvAvgWpm.text     = "%.0f".format(lifetime.avgWpm)
    }

    private fun refreshModelStatus() {
        val ready = SherpaOnnxSpeechEngine.isModelAvailable(this)
        tvModelStatus.text = if (ready) "Model ready: whisper-base.en" else "Model not downloaded"
        tvModelStatus.setTextColor(if (ready) 0xFF00B4D8.toInt() else 0xFF888888.toInt())
        btnDownload.visibility = if (ready) View.GONE else View.VISIBLE
        offlineSwitch.isEnabled = ready
    }

    private fun startDownload() {
        btnDownload.visibility = View.GONE
        progressBar.visibility = View.VISIBLE
        progressBar.isIndeterminate = false
        progressBar.max = 100
        tvDownloadStatus.visibility = View.VISIBLE
        tvDownloadStatus.text = "Connecting…"
        btnCancel.visibility = View.VISIBLE

        downloader = ModelDownloader(this).also {
            it.download(object : ModelDownloader.Listener {
                override fun onProgress(downloaded: Long, total: Long) {
                    val pct = if (total > 0) (downloaded * 100 / total).toInt() else 0
                    progressBar.progress = pct
                    tvDownloadStatus.text = "${formatBytes(downloaded)} / ${formatBytes(total)}"
                }
                override fun onExtracting() {
                    progressBar.isIndeterminate = true
                    tvDownloadStatus.text = "Extracting…"
                }
                override fun onComplete() {
                    resetDownloadUi()
                    refreshModelStatus()
                    VoiceOverlayService.instance?.reloadSpeechEngine()
                    Toast.makeText(this@SettingsActivity, "Model ready", Toast.LENGTH_SHORT).show()
                }
                override fun onError(message: String) {
                    resetDownloadUi()
                    Toast.makeText(
                        this@SettingsActivity,
                        "Download failed: $message",
                        Toast.LENGTH_LONG
                    ).show()
                }
            })
        }
    }

    private fun resetDownloadUi() {
        progressBar.visibility = View.GONE
        progressBar.isIndeterminate = false
        tvDownloadStatus.visibility = View.GONE
        btnCancel.visibility = View.GONE
        btnDownload.visibility = View.VISIBLE
    }

    private fun formatBytes(bytes: Long): String = when {
        bytes <= 0        -> "0 B"
        bytes < 1024      -> "$bytes B"
        bytes < 1_048_576 -> "${bytes / 1024} KB"
        else              -> "%.1f MB".format(bytes.toDouble() / 1_048_576)
    }

    override fun onDestroy() {
        super.onDestroy()
        downloader?.cancel()
    }
}
