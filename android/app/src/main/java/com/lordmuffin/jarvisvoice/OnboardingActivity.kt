package com.lordmuffin.jarvisvoice

import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.provider.Settings
import android.view.View
import android.widget.Button
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat

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

    override fun onResume() {
        super.onResume()
        if (step == 1) updatePermissionStates()
    }

    private fun showStep(s: Int) {
        step = s
        listOf(R.id.dot0, R.id.dot1, R.id.dot2).forEachIndexed { i, id ->
            val v = findViewById<View>(id)
            val lp = v.layoutParams
            lp.width = if (i == s) dpToPx(22) else dpToPx(7)
            v.layoutParams = lp
            v.setBackgroundColor(
                if (i == s) getColor(R.color.jv_accent) else 0x598B949E.toInt()
            )
        }
        findViewById<View>(R.id.step_0).visibility = if (s == 0) View.VISIBLE else View.GONE
        findViewById<View>(R.id.step_1).visibility = if (s == 1) View.VISIBLE else View.GONE
        findViewById<View>(R.id.step_2).visibility = if (s == 2) View.VISIBLE else View.GONE

        if (s == 1) updatePermissionStates()
    }

    private fun updatePermissionStates() {
        val micGranted = ContextCompat.checkSelfPermission(
            this, android.Manifest.permission.RECORD_AUDIO
        ) == PackageManager.PERMISSION_GRANTED

        val enabledServices = Settings.Secure.getString(
            contentResolver, Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES
        ) ?: ""
        val myA11yService = "$packageName/${JarvisAccessibilityService::class.java.name}"
        val a11yGranted = enabledServices.contains(myA11yService, ignoreCase = true)

        val overlayGranted = Settings.canDrawOverlays(this)

        applyGrantedStyle(R.id.btn_grant_mic, micGranted, isOverlay = false)
        applyGrantedStyle(R.id.btn_grant_a11y, a11yGranted, isOverlay = false)
        applyGrantedStyle(R.id.btn_grant_overlay, overlayGranted, isOverlay = true)

        // Show the All Files Access card only on Android 11+ where it requires explicit grant
        val cardStorage = findViewById<View>(R.id.card_storage)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            cardStorage.visibility = View.VISIBLE
            applyGrantedStyle(R.id.btn_grant_storage_onboard, Environment.isExternalStorageManager(), isOverlay = true)
        } else {
            cardStorage.visibility = View.GONE
        }
    }

    private fun applyGrantedStyle(btnId: Int, granted: Boolean, isOverlay: Boolean) {
        val btn = findViewById<Button>(btnId) ?: return
        if (granted) {
            btn.text = "✓ Granted"
            btn.setTextColor(getColor(R.color.jv_bg))
            btn.backgroundTintList = ContextCompat.getColorStateList(this, R.color.jv_success)
            btn.isEnabled = false
        } else {
            btn.text = "Grant"
            if (isOverlay) {
                btn.setTextColor(getColor(R.color.jv_text2))
                btn.backgroundTintList = ContextCompat.getColorStateList(this, R.color.jv_surface2)
            } else {
                btn.setTextColor(getColor(android.R.color.black))
                btn.backgroundTintList = ContextCompat.getColorStateList(this, R.color.jv_accent)
            }
            btn.isEnabled = true
        }
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

    fun onGrantStorage(v: View) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            startActivity(
                Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION).apply {
                    data = Uri.fromParts("package", packageName, null)
                }
            )
        }
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == 1) updatePermissionStates()
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
