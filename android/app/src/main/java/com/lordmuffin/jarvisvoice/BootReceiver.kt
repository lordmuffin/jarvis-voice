package com.lordmuffin.jarvisvoice

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import androidx.core.app.NotificationCompat

class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED) return

        if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            // Android 14+ (API 34) forbids starting a foreground service with
            // type=microphone from BOOT_COMPLETED — the app is not in an eligible
            // state (no visible activity, no bound FGS client). Attempting it throws
            // SecurityException inside the service's onCreate before startForeground
            // can complete, causing a crash-and-restart cycle.
            // Post a tap-to-restore notification instead. Tapping it launches
            // MainActivity which calls startForegroundService from a valid
            // user-initiated Activity context.
            postRestoreNotification(context)
        } else {
            context.startForegroundService(Intent(context, VoiceOverlayService::class.java))
        }
    }

    private fun postRestoreNotification(context: Context) {
        val pi = PendingIntent.getActivity(
            context, 0,
            Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
            PendingIntent.FLAG_IMMUTABLE
        )
        val nm = context.getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(
            NotificationChannel(
                VoiceOverlayService.CHANNEL_ID,
                "Jarvis Voice Overlay",
                NotificationManager.IMPORTANCE_DEFAULT
            )
        )
        val notif = NotificationCompat.Builder(context, VoiceOverlayService.CHANNEL_ID)
            .setContentTitle("Jarvis Voice")
            .setContentText("Tap to restore after reboot")
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setContentIntent(pi)
            .setAutoCancel(true)
            .build()
        nm.notify(VoiceOverlayService.NOTIF_RESTORE_ID, notif)
    }
}
