package com.lordmuffin.jarvisvoice

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.os.Bundle
import android.view.accessibility.AccessibilityNodeInfo

object TextInjector {

    // Always copies text to clipboard so it's accessible regardless of injection outcome.
    // If a focused node is provided, also attempts to inject directly into it.
    // Returns true only when the field took the text; false means it is on the clipboard alone.
    fun inject(node: AccessibilityNodeInfo?, text: String): Boolean {
        // Step 1: clipboard — unconditional
        VoiceOverlayService.instance?.let { ctx ->
            val cb = ctx.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
            cb.setPrimaryClip(ClipData.newPlainText("jarvis_transcript", text))
        }

        node ?: return false

        // Step 2: ACTION_SET_TEXT (replaces field content cleanly)
        val args = Bundle().apply {
            putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text)
        }
        if (node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)) return true

        // Step 3: paste from clipboard we just set
        return node.performAction(AccessibilityNodeInfo.ACTION_PASTE)
    }
}
