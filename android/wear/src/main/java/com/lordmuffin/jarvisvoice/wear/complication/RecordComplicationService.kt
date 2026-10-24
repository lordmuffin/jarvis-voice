package com.lordmuffin.jarvisvoice.wear.complication

import android.app.PendingIntent
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import androidx.wear.watchface.complications.data.ComplicationData
import androidx.wear.watchface.complications.data.ComplicationText
import androidx.wear.watchface.complications.data.ComplicationType
import androidx.wear.watchface.complications.data.CountUpTimeReference
import androidx.wear.watchface.complications.data.PlainComplicationText
import androidx.wear.watchface.complications.data.ShortTextComplicationData
import androidx.wear.watchface.complications.data.TimeDifferenceComplicationText
import androidx.wear.watchface.complications.data.TimeDifferenceStyle
import androidx.wear.watchface.complications.datasource.ComplicationDataSourceService
import androidx.wear.watchface.complications.datasource.ComplicationDataSourceUpdateRequester
import androidx.wear.watchface.complications.datasource.ComplicationRequest
import androidx.wear.watchface.complications.datasource.SuspendingComplicationDataSourceService
import com.lordmuffin.jarvisvoice.wear.R
import com.lordmuffin.jarvisvoice.wear.RecorderState
import com.lordmuffin.jarvisvoice.wear.ToggleActivity
import java.time.Instant

/**
 * A short-text complication: "REC" when idle, a running stopwatch while recording. Tapping it
 * toggles recording, same as the tile and the main button.
 */
class RecordComplicationService : SuspendingComplicationDataSourceService() {

    override fun getPreviewData(type: ComplicationType): ComplicationData? =
        if (type == ComplicationType.SHORT_TEXT) data(this, null, preview = true) else null

    override suspend fun onComplicationRequest(request: ComplicationRequest): ComplicationData? =
        if (request.complicationType == ComplicationType.SHORT_TEXT) data(this, RecorderState.active.value) else null

    companion object {
        fun requestUpdate(ctx: Context) {
            ComplicationDataSourceUpdateRequester
                .create(ctx, ComponentName(ctx, RecordComplicationService::class.java))
                .requestUpdateAll()
        }

        internal fun data(ctx: Context, active: RecorderState.Active?, preview: Boolean = false): ComplicationData {
            val tap = PendingIntent.getActivity(
                ctx, 0, Intent(ctx, ToggleActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
            )
            val text: ComplicationText = if (active != null) {
                TimeDifferenceComplicationText.Builder(
                    TimeDifferenceStyle.STOPWATCH,
                    CountUpTimeReference(Instant.ofEpochMilli(active.startedAtWallMs)),
                ).build()
            } else {
                PlainComplicationText.Builder(if (preview) "0:00" else ctx.getString(R.string.complication_idle)).build()
            }
            return ShortTextComplicationData.Builder(
                text,
                PlainComplicationText.Builder(ctx.getString(if (active != null) R.string.complication_stop_desc else R.string.complication_record_desc)).build(),
            )
                .setTitle(PlainComplicationText.Builder(ctx.getString(if (active != null) R.string.complication_rec else R.string.complication_label)).build())
                .setTapAction(tap)
                .build()
        }
    }
}
