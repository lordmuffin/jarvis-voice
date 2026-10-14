package com.lordmuffin.jarvisvoice

import android.Manifest
import android.content.ComponentName
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Bundle
import android.provider.Settings
import android.view.accessibility.AccessibilityManager
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat

class MainActivity : AppCompatActivity() {

    private val requestAudio = registerForActivityResult(ActivityResultContracts.RequestPermission()) {
        checkAndProceed()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val prefs = getSharedPreferences(VoiceOverlayService.PREF_FILE, MODE_PRIVATE)
        if (!prefs.getBoolean("onboarding_done", false)) {
            startActivity(Intent(this, OnboardingActivity::class.java))
            finish()
            return
        }
        checkAndProceed()
    }

    override fun onResume() {
        super.onResume()
        checkAndProceed()
        // App is alive and interactive — clear the crash guard flag
        (application as JarvisApp).markCleanLaunch()
    }

    private fun checkAndProceed() {
        when {
            !Settings.canDrawOverlays(this) -> promptOverlay()
            ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED -> requestAudio.launch(Manifest.permission.RECORD_AUDIO)
            !isAccessibilityEnabled() -> promptAccessibility()
            else -> {
                startForegroundService(Intent(this, VoiceOverlayService::class.java))
                startActivity(Intent(this, SettingsActivity::class.java))
                finish()
            }
        }
    }

    private fun promptOverlay() {
        AlertDialog.Builder(this)
            .setTitle(R.string.models_main_overlay_title)
            .setMessage(R.string.models_main_overlay_message)
            .setPositiveButton(R.string.models_main_grant) { _, _ ->
                startActivity(Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:$packageName")))
            }
            .setCancelable(false)
            .show()
    }

    private fun promptAccessibility() {
        AlertDialog.Builder(this)
            .setTitle(R.string.models_main_a11y_title)
            .setMessage(R.string.models_main_a11y_message)
            .setPositiveButton(R.string.models_main_open_settings) { _, _ ->
                startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
            }
            .setCancelable(false)
            .show()
    }

    private fun isAccessibilityEnabled(): Boolean {
        val am = getSystemService(ACCESSIBILITY_SERVICE) as AccessibilityManager
        val enabledServices = Settings.Secure.getString(
            contentResolver,
            Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES
        ) ?: return false
        val component = ComponentName(this, JarvisAccessibilityService::class.java)
        return enabledServices.split(":").any {
            it.equals(component.flattenToString(), ignoreCase = true)
        }
    }
}
