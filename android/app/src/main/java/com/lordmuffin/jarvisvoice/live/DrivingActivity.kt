package com.lordmuffin.jarvisvoice.live

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import androidx.core.content.ContextCompat
import com.lordmuffin.jarvisvoice.live.DrivingTrigger.Action
import com.lordmuffin.jarvisvoice.live.protocol.SessionMode
import java.time.LocalDateTime

/**
 * No-UI entry point for the launcher shortcut and the Quick Settings tile: starts a solo Live
 * session (or marks the current one) and finishes. Shows over the lock screen, so nothing needs
 * unlocking. Being a visible activity is what lets the microphone foreground service start.
 */
class DrivingActivity : Activity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setShowWhenLocked(true)
        val settings = LiveSettings(this)
        val micGranted = ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) ==
            PackageManager.PERMISSION_GRANTED
        when (DrivingTrigger.decide(LiveSessionService.isRecording, micGranted, settings.isConfigured)) {
            Action.START -> LiveSessionService.start(this, SessionMode.SOLO, DrivingTrigger.title(LocalDateTime.now()), false, builtInMic = true)
            Action.START_LOCAL -> LiveSessionService.start(this, SessionMode.SOLO, DrivingTrigger.title(LocalDateTime.now()), true, builtInMic = true)
            Action.MARK -> LiveSessionService.marker(this)
            Action.OPEN_RECORD -> startActivity(Intent(this, LiveActivity::class.java))
        }
        finish()
    }
}
