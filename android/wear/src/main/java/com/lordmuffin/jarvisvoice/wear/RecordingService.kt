package com.lordmuffin.jarvisvoice.wear

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import android.os.SystemClock
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import com.lordmuffin.jarvisvoice.wear.complication.RecordComplicationService
import com.lordmuffin.jarvisvoice.wear.sync.SyncScheduler
import com.lordmuffin.jarvisvoice.wear.tile.RecordTileService

/**
 * Owns the microphone. A foreground service of type `microphone`, so recording continues with the
 * screen off and the app out of sight. Start and stop come from the main screen, the tile, the
 * complication and the notification. [RecordingSession] holds the recording itself.
 *
 * A recording is queued the moment it stops; the queue (not this service) then gets it to the
 * server, and never deletes it before the server acks.
 */
class RecordingService : Service() {
    private lateinit var session: RecordingSession
    private var wakeLock: PowerManager.WakeLock? = null
    private var latestStartId = 0

    private val handler = Handler(Looper.getMainLooper())
    private val progressCheck = object : Runnable {
        override fun run() {
            if (session.stalled()) {
                Log.e(TAG, "recording stopped growing")
                finish(RecorderState.Failure.STOPPED_EARLY)
            } else {
                handler.postDelayed(this, RecordingSession.PROGRESS_CHECK_MS)
            }
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        val graph = WearGraph.get(this)
        session = RecordingSession(
            AacRecorder(this, graph.recordingsDir),
            graph.store,
            System::currentTimeMillis,
            SystemClock::elapsedRealtime,
        ) { started ->
            if (started) Haptics.started(this) else Haptics.stopped(this)
            RecordTileService.requestUpdate(this)
            RecordComplicationService.requestUpdate(this)
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        latestStartId = startId
        when {
            intent?.action == ACTION_START -> begin()
            session.isRecording -> finish()
            else -> stopSelf(startId)
        }
        return START_NOT_STICKY
    }

    private fun begin() {
        // Every startForegroundService must be answered with startForeground, even a repeat tap on a
        // running recording, or the system kills the app.
        val wall = RecorderState.active.value?.startedAtWallMs ?: System.currentTimeMillis()
        try {
            ServiceCompat.startForeground(this, NOTIF_ID, notification(wall), ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE)
        } catch (e: RuntimeException) {
            // SecurityException or ForegroundServiceStartNotAllowedException: the system refused the
            // microphone because the app was not visible. Say so rather than crash.
            Log.e(TAG, "cannot enter the foreground", e)
            if (!session.isRecording) {
                RecorderState.failed(RecorderState.Failure.MIC_UNAVAILABLE)
                stopSelf(latestStartId)
            }
            return
        }
        if (session.isRecording) return
        if (!session.start { finish(it) }) {
            stopForeground(STOP_FOREGROUND_REMOVE)
            stopSelf(latestStartId)
            return
        }
        wakeLock = (getSystemService(POWER_SERVICE) as PowerManager)
            .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "jarvis:recording")
            .apply { acquire(RecordingSession.MAX_RECORDING_MS + 60_000L) }
        handler.postDelayed(progressCheck, RecordingSession.PROGRESS_CHECK_MS)
    }

    private fun finish(failure: RecorderState.Failure? = null) {
        handler.removeCallbacks(progressCheck)
        session.stop(failure)
        wakeLock?.let { if (it.isHeld) it.release() }
        wakeLock = null
        stopForeground(STOP_FOREGROUND_REMOVE)
        SyncScheduler.kick(this)
        // With the id, not bare stopSelf(): a start already on its way must still reach onStartCommand,
        // or the system kills the app for a startForegroundService that was never answered.
        stopSelf(latestStartId)
    }

    override fun onDestroy() {
        // Killed or stopped by the system mid-recording: keep what is on disk.
        if (session.isRecording) finish()
        super.onDestroy()
    }

    private fun notification(startedAtWallMs: Long): Notification {
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(
            NotificationChannel(CHANNEL_ID, getString(R.string.wear_notif_channel), NotificationManager.IMPORTANCE_LOW),
        )
        val open = PendingIntent.getActivity(
            this, 0, Intent(this, WearMainActivity::class.java), PendingIntent.FLAG_IMMUTABLE,
        )
        val stop = PendingIntent.getService(
            this, 1, intentFor(this, ACTION_STOP), PendingIntent.FLAG_IMMUTABLE,
        )
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.rec_dot)
            .setContentTitle(getString(R.string.wear_state_recording))
            .setOngoing(true)
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .setUsesChronometer(true)
            .setWhen(startedAtWallMs)
            .setContentIntent(open)
            .addAction(0, getString(R.string.wear_stop_capture), stop)
            .build()
    }

    companion object {
        private const val TAG = "WearRecording"
        private const val CHANNEL_ID = "recording"
        private const val NOTIF_ID = 1
        const val ACTION_START = "com.lordmuffin.jarvisvoice.wear.START"
        const val ACTION_STOP = "com.lordmuffin.jarvisvoice.wear.STOP"

        private fun intentFor(ctx: Context, action: String) =
            Intent(ctx, RecordingService::class.java).setAction(action)

        /** Starts a recording. Call from a visible activity: the system only gives one the microphone. */
        fun start(ctx: Context) {
            try {
                ctx.startForegroundService(intentFor(ctx, ACTION_START))
            } catch (e: IllegalStateException) {   // includes ForegroundServiceStartNotAllowedException
                Log.e(TAG, "cannot start the recording service", e)
                RecorderState.failed(RecorderState.Failure.MIC_UNAVAILABLE)
            }
        }

        /** Stops the running recording. A plain start: the service is in the foreground already. */
        fun stop(ctx: Context) {
            ctx.startService(intentFor(ctx, ACTION_STOP))
        }
    }
}
