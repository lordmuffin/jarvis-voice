package com.lordmuffin.jarvisvoice

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.AccessibilityServiceInfo
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import android.view.accessibility.AccessibilityWindowInfo

class JarvisAccessibilityService : AccessibilityService() {

    companion object {
        var instance: JarvisAccessibilityService? = null
    }

    // Tracks whether an accessible editable field has focus. Used to capture
    // lastFocusedNode for direct injection. NOT used to gate showOverlay() —
    // keyboard visibility is the authoritative trigger (handles Electron/WebView
    // apps whose inputs don't surface editable accessibility nodes).
    private var hasEditableFocus = false

    override fun onServiceConnected() {
        instance = this
        serviceInfo = serviceInfo.apply {
            eventTypes = AccessibilityEvent.TYPE_VIEW_FOCUSED or
                    AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED or
                    AccessibilityEvent.TYPE_WINDOWS_CHANGED
            feedbackType = AccessibilityServiceInfo.FEEDBACK_GENERIC
            flags = AccessibilityServiceInfo.FLAG_RETRIEVE_INTERACTIVE_WINDOWS
            notificationTimeout = 100
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        instance = null
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent) {
        when (event.eventType) {
            AccessibilityEvent.TYPE_VIEW_FOCUSED -> {
                val node = event.source ?: return
                if (node.isEditable) {
                    hasEditableFocus = true
                    VoiceOverlayService.lastFocusedNode = AccessibilityNodeInfo.obtain(node)
                    // Keyboard may already be visible when focus moves between fields
                    if (isKeyboardVisible()) {
                        VoiceOverlayService.instance?.showOverlay()
                    }
                } else {
                    hasEditableFocus = false
                    VoiceOverlayService.lastFocusedNode = null
                }
            }

            // Fires when any window appears/disappears, including the IME.
            // Show the overlay whenever the keyboard is up — this covers Electron
            // and WebView-based apps that don't emit editable focus events.
            AccessibilityEvent.TYPE_WINDOWS_CHANGED -> {
                if (isKeyboardVisible()) {
                    VoiceOverlayService.instance?.showOverlay()
                } else {
                    VoiceOverlayService.instance?.hideOverlay()
                }
            }

            AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED -> {
                // App switched / activity changed — reset editable focus tracking
                if (event.source == null) {
                    hasEditableFocus = false
                    VoiceOverlayService.lastFocusedNode = null
                }
            }
        }
    }

    private fun isKeyboardVisible(): Boolean =
        windows.any { it.type == AccessibilityWindowInfo.TYPE_INPUT_METHOD }

    override fun onInterrupt() {
        VoiceOverlayService.instance?.hideOverlay()
    }
}
