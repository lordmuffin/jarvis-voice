package com.lordmuffin.jarvisvoice.ui

import android.view.View
import android.widget.TextView
import androidx.annotation.ColorRes
import androidx.annotation.StringRes
import com.lordmuffin.jarvisvoice.R
import com.lordmuffin.jarvisvoice.live.ConnectionChip
import com.lordmuffin.jarvisvoice.live.ConnectionKind

@get:StringRes
val ConnectionKind.label: Int
    get() = when (this) {
        ConnectionKind.IDLE -> R.string.conn_idle
        ConnectionKind.CONNECTING -> R.string.conn_connecting
        ConnectionKind.CONNECTED -> R.string.conn_connected
        ConnectionKind.RECONNECTING -> R.string.conn_reconnecting
        ConnectionKind.LOCAL_ONLY -> R.string.conn_local_only
        ConnectionKind.DEGRADED -> R.string.conn_degraded
        ConnectionKind.OFFLINE -> R.string.conn_offline
        ConnectionKind.FAILED -> R.string.conn_failed
    }

/** State colour carries state only; the word and glyph always come with it. */
@get:ColorRes
val ConnectionKind.color: Int
    get() = when (this) {
        ConnectionKind.CONNECTED -> R.color.inst_accent
        ConnectionKind.RECONNECTING, ConnectionKind.DEGRADED -> R.color.inst_warning
        ConnectionKind.FAILED -> R.color.inst_error
        ConnectionKind.LOCAL_ONLY -> R.color.inst_text
        ConnectionKind.IDLE, ConnectionKind.CONNECTING, ConnectionKind.OFFLINE -> R.color.inst_text_2
    }

/** Fills a `Widget.Inst.Chip` TextView with [chip]. */
fun TextView.bindConnection(chip: ConnectionChip) {
    setText(chip.kind.label)
    setTextColor(context.getColor(chip.kind.color))
    visibility = View.VISIBLE
}

/** Fills a chip for a session row whose recorder stopped sending audio. */
fun TextView.bindOffline() {
    setText(ConnectionKind.OFFLINE.label)
    setTextColor(context.getColor(ConnectionKind.OFFLINE.color))
    contentDescription = context.getString(R.string.conn_offline_hint)
}
