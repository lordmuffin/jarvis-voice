package com.lordmuffin.jarvisvoice

import android.content.Intent
import android.os.Bundle
import android.text.Editable
import android.text.TextWatcher
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.appcompat.widget.SwitchCompat
import com.lordmuffin.jarvisvoice.speech.SttModelManager
import com.lordmuffin.jarvisvoice.speech.SpeechEngineFactory

class SettingsActivity : AppCompatActivity() {

    private lateinit var offlineSwitch: SwitchCompat
    private lateinit var clipboardNotifySwitch: SwitchCompat
    private lateinit var tvActiveSttModel: TextView
    private lateinit var tvActiveLlmModel: TextView
    private lateinit var etHfToken: EditText

    // Stats views
    private lateinit var tvLastWords: TextView
    private lateinit var tvLastWpm: TextView
    private lateinit var tvTotalWords: TextView
    private lateinit var tvAvgWpm: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_settings)
        supportActionBar?.title = "Jarvis Voice Settings"

        offlineSwitch         = findViewById(R.id.switch_offline_stt)
        clipboardNotifySwitch = findViewById(R.id.switch_clipboard_notify)
        tvActiveSttModel  = findViewById(R.id.tv_active_stt_model)
        tvLastWords       = findViewById(R.id.tv_stat_last_words)
        tvLastWpm         = findViewById(R.id.tv_stat_last_wpm)
        tvTotalWords      = findViewById(R.id.tv_stat_total_words)
        tvAvgWpm          = findViewById(R.id.tv_stat_avg_wpm)
        tvActiveLlmModel  = findViewById(R.id.tv_active_llm_model)
        etHfToken         = findViewById(R.id.et_hf_token)

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

        etHfToken.setText(prefs.getString("hf_token", ""))
        etHfToken.addTextChangedListener(object : TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) = Unit
            override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) = Unit
            override fun afterTextChanged(s: Editable?) {
                prefs.edit().putString("hf_token", s?.toString()?.trim() ?: "").apply()
            }
        })

        findViewById<Button>(R.id.btn_open_stt_model_manager).setOnClickListener {
            startActivity(Intent(this, SttModelManagerActivity::class.java))
        }

        findViewById<Button>(R.id.btn_open_model_manager).setOnClickListener {
            startActivity(Intent(this, ModelManagerActivity::class.java))
        }

        findViewById<Button>(R.id.btn_open_history).setOnClickListener {
            startActivity(Intent(this, HistoryActivity::class.java))
        }
        findViewById<Button>(R.id.btn_open_dictionary).setOnClickListener {
            startActivity(Intent(this, DictionaryActivity::class.java))
        }
        findViewById<Button>(R.id.btn_open_debug_log).setOnClickListener {
            startActivity(Intent(this, DebugLogActivity::class.java))
        }

        refreshStats()
    }

    override fun onResume() {
        super.onResume()
        refreshStats()
        refreshLlmLabel()
        refreshSttLabel()
    }

    private fun refreshSttLabel() {
        val config = SttModelManager(this).getActiveConfig()
        tvActiveSttModel.text = if (config != null) {
            "Active: ${config.displayName}"
        } else {
            "No model downloaded"
        }
    }

    private fun refreshLlmLabel() {
        val llmMgr  = LlmModelManager(this)
        val activeId = llmMgr.getActiveModelId()
        val config   = ModelRegistry.MODELS.find { it.id == activeId }
        tvActiveLlmModel.text = if (config != null) {
            "Enhancement: ${config.displayName}"
        } else {
            "Enhancement: Off"
        }
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

}
