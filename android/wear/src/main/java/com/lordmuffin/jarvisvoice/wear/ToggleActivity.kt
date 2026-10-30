package com.lordmuffin.jarvisvoice.wear

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import androidx.core.content.ContextCompat

/**
 * Invisible trampoline for the tile, the complication and the "Jarvis record" launcher entry (the one
 * a watch button is mapped to). Starting a microphone foreground service needs a visible activity,
 * and these surfaces can only launch activities, so this is the activity.
 * Without the mic permission it hands over to the main screen, which asks for it.
 */
class ToggleActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val granted = ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED
        if (granted || RecorderState.active.value != null) {
            RecordingService.toggle(this)
        } else {
            startActivity(Intent(this, WearMainActivity::class.java).putExtra(WearMainActivity.EXTRA_START_AFTER_PERMISSION, true))
        }
        finish()
    }
}
