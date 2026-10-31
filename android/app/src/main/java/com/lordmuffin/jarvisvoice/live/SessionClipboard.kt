package com.lordmuffin.jarvisvoice.live

import android.app.Activity
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.os.Build
import com.lordmuffin.jarvisvoice.R
import com.lordmuffin.jarvisvoice.ui.InstToast

/** The session detail's copy buttons. */
internal object SessionClipboard {

    fun copy(activity: Activity, text: String) {
        val cb = activity.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        cb.setPrimaryClip(ClipData.newPlainText("jarvis_session", text))
        // Android 13+ shows its own confirmation for every copy; a second one would repeat it.
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) InstToast.show(activity, R.string.detail_copied)
    }
}
