package com.lordmuffin.jarvisvoice.live

import android.content.Context
import com.lordmuffin.jarvisvoice.R
import com.lordmuffin.jarvisvoice.ui.compose.RecentRow
import com.lordmuffin.jarvisvoice.ui.compose.Tone
import com.lordmuffin.jarvisvoice.ui.label
import java.text.DateFormat
import java.util.Date

/** Labels shared by the history list and the detail screen. */
internal object SessionLabels {

    fun statusLabel(ctx: Context, status: String): String = when (status) {
        RemoteSession.STATUS_LIVE -> ctx.getString(R.string.history_status_live)
        RemoteSession.STATUS_FINALIZING -> ctx.getString(R.string.history_status_finalizing)
        RemoteSession.STATUS_DONE -> ctx.getString(R.string.history_status_done)
        RemoteSession.STATUS_FAILED -> ctx.getString(R.string.history_status_failed)
        else -> status
    }

    /** A live session that is not receiving audio reads "Offline" (as on web), not "Live". */
    fun statusText(ctx: Context, status: String, streaming: Boolean = true): String =
        if (ConnectionChip.isOffline(status, streaming)) ctx.getString(ConnectionKind.OFFLINE.label)
        else statusLabel(ctx, status)

    fun tone(status: String, streaming: Boolean = true): Tone = when {
        ConnectionChip.isOffline(status, streaming) -> Tone.Neutral
        status == RemoteSession.STATUS_LIVE -> Tone.Accent
        status == RemoteSession.STATUS_FINALIZING -> Tone.Warning
        status == RemoteSession.STATUS_DONE -> Tone.Success
        status == RemoteSession.STATUS_FAILED -> Tone.Error
        else -> Tone.Neutral
    }

    /** One history row. */
    fun row(ctx: Context, s: RemoteSession) = RecentRow(
        id = s.id,
        title = s.title ?: ctx.getString(R.string.history_untitled),
        status = statusText(ctx, s.status, s.streaming),
        statusTone = tone(s.status, s.streaming),
        meta = meta(ctx, s),
    )

    fun errorText(ctx: Context, e: Exception): String = when {
        e is LiveHttpException && e.status == 401 -> ctx.getString(R.string.history_unauthorized)
        e is LiveHttpException -> ctx.getString(R.string.history_error, ctx.getString(R.string.livesess_http, e.status))
        else -> ctx.getString(R.string.history_error, e.message ?: e.javaClass.simpleName)
    }

    fun modeLabel(ctx: Context, mode: String): String =
        ctx.getString(if (mode == "meeting") R.string.history_mode_meeting else R.string.history_mode_solo)

    /** "Oct 11, 2026, 9:14 AM · 42:10 · Meeting" */
    fun meta(ctx: Context, s: RemoteSession): String = listOfNotNull(
        s.startedAt?.let { DateFormat.getDateTimeInstance(DateFormat.MEDIUM, DateFormat.SHORT).format(Date(it.toEpochMilli())) },
        s.durationMs?.let { RemoteSessions.formatDuration(it) }
            ?: if (s.isInProgress) ctx.getString(R.string.history_in_progress) else null,
        modeLabel(ctx, s.mode),
    ).joinToString(" · ")
}
