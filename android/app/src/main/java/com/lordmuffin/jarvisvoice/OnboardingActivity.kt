package com.lordmuffin.jarvisvoice

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.provider.Settings
import android.view.View
import androidx.appcompat.app.AppCompatActivity

class OnboardingActivity : AppCompatActivity() {

    private var step = 0
    private val prefs get() = getSharedPreferences(VoiceOverlayService.PREF_FILE, MODE_PRIVATE)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (prefs.getBoolean("onboarding_done", false)) {
            finish()
            return
        }
        setContentView(R.layout.activity_onboarding)
        showStep(0)
    }

    private fun showStep(s: Int) {
        step = s
        // Update dot indicators
        listOf(R.id.dot0, R.id.dot1, R.id.dot2).forEachIndexed { i, id ->
            val v = findViewById<View>(id)
            val lp = v.layoutParams
            lp.width = if (i == s) dpToPx(22) else dpToPx(7)
            v.layoutParams = lp
            v.setBackgroundColor(
                if (i == s) getColor(R.color.jv_accent) else 0x598B949E.toInt()
            )
        }
        // Show/hide step containers
        findViewById<View>(R.id.step_0).visibility = if (s == 0) View.VISIBLE else View.GONE
        findViewById<View>(R.id.step_1).visibility = if (s == 1) View.VISIBLE else View.GONE
        findViewById<View>(R.id.step_2).visibility = if (s == 2) View.VISIBLE else View.GONE
    }

    fun onGetStarted(v: View) = showStep(1)

    fun onGrantMic(v: View) {
        requestPermissions(arrayOf(android.Manifest.permission.RECORD_AUDIO), 1)
    }

    fun onGrantAccessibility(v: View) {
        startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
    }

    fun onGrantOverlay(v: View) {
        startActivity(Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:$packageName")))
    }

    fun onContinue(v: View) = showStep(2)

    fun onFinish(v: View) {
        prefs.edit().putBoolean("onboarding_done", true).apply()
        startActivity(Intent(this, MainActivity::class.java))
        finish()
    }

    fun onOpenSettings(v: View) {
        prefs.edit().putBoolean("onboarding_done", true).apply()
        startActivity(Intent(this, SettingsActivity::class.java))
        finish()
    }

    private fun dpToPx(dp: Int) = (dp * resources.displayMetrics.density).toInt()
}
