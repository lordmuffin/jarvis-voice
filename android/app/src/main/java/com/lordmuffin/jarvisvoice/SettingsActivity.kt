package com.lordmuffin.jarvisvoice

import android.os.Bundle
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.appcompat.widget.SwitchCompat
import com.lordmuffin.jarvisvoice.speech.SherpaOnnxSpeechEngine
import com.lordmuffin.jarvisvoice.speech.SpeechEngineFactory

class SettingsActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_settings)
        supportActionBar?.title = "Jarvis Voice Settings"

        val offlineSwitch = findViewById<SwitchCompat>(R.id.switch_offline_stt)
        val statusText = findViewById<TextView>(R.id.tv_model_status)

        val modelReady = SherpaOnnxSpeechEngine.isModelAvailable(this)
        offlineSwitch.isChecked = SpeechEngineFactory.isOfflineModeEnabled(this)
        statusText.text = if (modelReady) {
            "Model ready: whisper-base.en (NNAPI)"
        } else {
            "Model not found — run ./download-models.sh then rebuild"
        }
        statusText.setTextColor(if (modelReady) 0xFF00B4D8.toInt() else 0xFFFF5555.toInt())

        offlineSwitch.setOnCheckedChangeListener { _, checked ->
            SpeechEngineFactory.setOfflineMode(this, checked)
            VoiceOverlayService.instance?.reloadSpeechEngine()
        }
    }
}
