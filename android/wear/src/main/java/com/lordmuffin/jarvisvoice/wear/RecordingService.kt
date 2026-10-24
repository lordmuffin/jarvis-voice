package com.lordmuffin.jarvisvoice.wear

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.IBinder
import android.os.PowerManager
import android.os.SystemClock
import android.util.Log
import androidx.core.app.NotificationCompat
import com.lordmuffin.jarvisvoice.wear.complication.RecordComplicationService
import com.lordmuffin.jarvisvoice.wear.sync.SyncScheduler
import com.lordmuffin.jarvisvoice.wear.tile.RecordTileService
import com.lordmuffin.jarvisvoice.wearcommon.sync.QueueItem

/**
 * Owns the microphone. A foreground service of type `microphone`, so recording continues with the
 * screen off and the app out of sight. Start and stop come from the main screen, the tile, the
 * complication and the notification, all through [toggle].
 *
 * A recording is queued the moment it stops; the queue (not this service) then gets it to the
 * server, and never deletes it before the server acks.
 */
class RecordingService : Service() {
    private lateinit var recorder: AacRecorder
    private var wakeLock: PowerManager.WakeLock? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        val graph = WearGraph.get(this)
        recorder = AacRecorder(this, graph.recordingsDir)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_START -> if (!recorder.isRecording) startRecording()
            ACTION_STOP -> stopRecording()
            else -> if (recorder.isRecording) stopRecording() else startRecording()   // ACTION_TOGGLE
        }
        return START_NOT_STICKY
    }

    private fun startRecording() {
        val now = System.currentTimeMillis()
        val id = "w-$now"
        val elapsed = SystemClock.elapsedRealtime()
        // startForeground must come first or the system kills the service for not promoting in time.
        startForeground(NOTIF_ID, notification(now), ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE)
        try {
            recorder.start(id, MAX_RECORDING_MS) { stopRecording() }
        } catch (e: Exception) {
            Log.e(TAG, "cannot start recording", e)
            stopForeground(STOP_FOREGROUND_REMOVE)
            stopSelf()
            return
        }
        wakeLock = (getSystemService(POWER_SERVICE) as PowerManager)
            .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "jarvis:recording")
            .apply { acquire(MAX_RECORDING_MS + 60_000L) }
        RecorderState.started(RecorderState.Active(id, now, elapsed))
        Haptics.started(this)
        refreshSurfaces()
    }

    private fun stopRecording() {
        val active = RecorderState.active.value
        val file = recorder.stop()
        RecorderState.stopped()
        wakeLock?.let { if (it.isHeld) it.release() }
        wakeLock = null
        if (active != null && file != null) {
            val durationMs = RecorderState.elapsedMs(SystemClock.elapsedRealtime(), active)
            if (durationMs < MIN_RECORDING_MS || file.length() == 0L) {
                file.delete()   // an accidental double tap: nothing worth keeping
            } else {
                // Queue it before anything else can fail. The file is already on disk.
                WearGraph.get(this).store.put(
                    QueueItem(active.id, file.absolutePath, active.startedAtWallMs, durationMs, file.length()),
                )
            }
        }
        Haptics.stopped(this)
        stopForeground(STOP_FOREGROUND_REMOVE)
        refreshSurfaces()
        SyncScheduler.kick(this)
        stopSelf()
    }

    override fun onDestroy() {
        // Killed or stopped by the system mid-recording: keep what is on disk.
        if (recorder.isRecording) stopRecording()
        super.onDestroy()
    }

    private fun refreshSurfaces() {
        RecordTileService.requestUpdate(this)
        RecordComplicationService.requestUpdate(this)
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
        const val ACTION_TOGGLE = "com.lordmuffin.jarvisvoice.wear.TOGGLE"
        const val ACTION_START = "com.lordmuffin.jarvisvoice.wear.START"
        const val ACTION_STOP = "com.lordmuffin.jarvisvoice.wear.STOP"

        /** Safety cap: two hours is ~21 MB. Beyond that the recording ends and is queued like any other. */
        const val MAX_RECORDING_MS = 2 * 60 * 60 * 1000

        /** Shorter than this is a mis-tap. */
        const val MIN_RECORDING_MS = 700L

        private fun intentFor(ctx: Context, action: String) =
            Intent(ctx, RecordingService::class.java).setAction(action)

        /** Starts recording if idle, stops if recording. Call from a foreground context (an activity, a notification action). */
        fun toggle(ctx: Context) {
            ctx.startForegroundService(intentFor(ctx, ACTION_TOGGLE))
        }
    }
}
