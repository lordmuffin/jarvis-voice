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
        checkAndProceed()
    }

    override fun onResume() {
        super.onResume()
        checkAndProceed()
    }

    private fun checkAndProceed() {
        when {
            !Settings.canDrawOverlays(this) -> promptOverlay()
            ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED -> requestAudio.launch(Manifest.permission.RECORD_AUDIO)
            !isAccessibilityEnabled() -> promptAccessibility()
            else -> {
                startForegroundService(Intent(this, VoiceOverlayService::class.java))
                finish()
            }
        }
    }

    private fun promptOverlay() {
        AlertDialog.Builder(this)
            .setTitle("Overlay Permission Required")
            .setMessage("Jarvis Voice needs permission to draw over other apps to show the dictation pill.")
            .setPositiveButton("Grant") { _, _ ->
                startActivity(Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:$packageName")))
            }
            .setCancelable(false)
            .show()
    }

    private fun promptAccessibility() {
        AlertDialog.Builder(this)
            .setTitle("Accessibility Service Required")
            .setMessage("Enable 'Jarvis Voice' in Accessibility Settings so the overlay knows when text fields are focused.")
            .setPositiveButton("Open Settings") { _, _ ->
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
