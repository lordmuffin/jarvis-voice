package com.lordmuffin.jarvisvoice.notify

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import androidx.core.app.NotificationCompat
import androidx.work.Constraints
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.Worker
import androidx.work.WorkerParameters
import androidx.work.WorkManager
import com.lordmuffin.jarvisvoice.R
import com.lordmuffin.jarvisvoice.VoiceChatActivity
import java.util.concurrent.TimeUnit
import kotlin.math.abs

class NotifyWorker(ctx: Context, params: WorkerParameters) : Worker(ctx, params) {

    override fun doWork(): Result {
        val repo = NotificationRepository(applicationContext)
        val due = repo.fetchPending()
        due.forEach { show(it) }
        return Result.success()
    }

    private fun show(n: AppNotification) {
        val nm = applicationContext.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        ensureChannel(nm)

        val tap = PendingIntent.getActivity(
            applicationContext, 0,
            Intent(applicationContext, VoiceChatActivity::class.java).apply {
                flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
            },
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )

        val notif = NotificationCompat.Builder(applicationContext, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_mic)
            .setContentTitle(n.title)
            .setContentText(n.body.ifBlank { null })
            .setStyle(if (n.body.isNotBlank()) NotificationCompat.BigTextStyle().bigText(n.body) else null)
            .setContentIntent(tap)
            .setAutoCancel(true)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .build()

        nm.notify(abs(n.id.hashCode()), notif)
    }

    private fun ensureChannel(nm: NotificationManager) {
        if (nm.getNotificationChannel(CHANNEL_ID) == null) {
            nm.createNotificationChannel(
                NotificationChannel(CHANNEL_ID, "Jarvis Reminders", NotificationManager.IMPORTANCE_HIGH).apply {
                    description = "Scheduled reminders from Kai"
                }
            )
        }
    }

    companion object {
        const val CHANNEL_ID = "jarvis_reminders"
        private const val WORK_NAME = "jarvis_notify_poll"

        fun schedule(context: Context) {
            val req = PeriodicWorkRequestBuilder<NotifyWorker>(15, TimeUnit.MINUTES)
                .setConstraints(
                    Constraints.Builder()
                        .setRequiredNetworkType(NetworkType.CONNECTED)
                        .build()
                )
                .build()
            WorkManager.getInstance(context).enqueueUniquePeriodicWork(
                WORK_NAME,
                ExistingPeriodicWorkPolicy.KEEP,
                req,
            )
        }
    }
}
