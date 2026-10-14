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
import com.lordmuffin.jarvisvoice.live.LiveActivity

class MainActivity : AppCompatActivity() {

    private val requestAudio = registerForActivityResult(ActivityResultContracts.RequestPermission()) {
        checkAndProceed()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        checkAndProceed()
    }

    override fun onResume() {
        super.onResume()
        checkAndProceed()
        // App is alive and interactive — clear the crash guard flag
        (application as JarvisApp).markCleanLaunch()
    }

    private fun checkAndProceed() {
        val prefs = getSharedPreferences(VoiceOverlayService.PREF_FILE, MODE_PRIVATE)
        val step = LauncherRoute.next(
            onboardingDone = prefs.getBoolean("onboarding_done", false),
            overlayOk = Settings.canDrawOverlays(this),
            micOk = ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED,
            accessibilityOk = isAccessibilityEnabled(),
        )
        when (step) {
            LauncherRoute.Step.ONBOARDING -> {
                startActivity(Intent(this, OnboardingActivity::class.java))
                finish()
            }
            LauncherRoute.Step.GRANT_OVERLAY -> promptOverlay()
            LauncherRoute.Step.GRANT_MIC -> requestAudio.launch(Manifest.permission.RECORD_AUDIO)
            LauncherRoute.Step.GRANT_ACCESSIBILITY -> promptAccessibility()
            LauncherRoute.Step.RECORD -> {
                startForegroundService(Intent(this, VoiceOverlayService::class.java))
                // Record is home: the icon lands here once set up (it used to land on Settings, or nowhere).
                startActivity(Intent(this, LiveActivity::class.java))
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
