package com.lordmuffin.jarvisvoice

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.AccessibilityServiceInfo
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import android.view.accessibility.AccessibilityWindowInfo

class JarvisAccessibilityService : AccessibilityService() {

    override fun onServiceConnected() {
        serviceInfo = serviceInfo.apply {
            eventTypes = AccessibilityEvent.TYPE_VIEW_FOCUSED or
                    AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED or
                    AccessibilityEvent.TYPE_WINDOWS_CHANGED
            feedbackType = AccessibilityServiceInfo.FEEDBACK_GENERIC
            flags = AccessibilityServiceInfo.FLAG_RETRIEVE_INTERACTIVE_WINDOWS
            notificationTimeout = 100
        }
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent) {
        when (event.eventType) {
            AccessibilityEvent.TYPE_VIEW_FOCUSED -> {
                val node = event.source ?: return
                if (node.isEditable) {
                    VoiceOverlayService.lastFocusedNode = AccessibilityNodeInfo.obtain(node)
                    // Only surface the overlay when the soft keyboard is actually up
                    if (isKeyboardVisible()) {
                        VoiceOverlayService.instance?.showOverlay()
                    }
                }
            }

            // API 28+ — fires when any window appears/disappears, including IME
            AccessibilityEvent.TYPE_WINDOWS_CHANGED -> {
                if (isKeyboardVisible()) {
                    if (VoiceOverlayService.lastFocusedNode != null) {
                        VoiceOverlayService.instance?.showOverlay()
                    }
                } else {
                    VoiceOverlayService.lastFocusedNode = null
                    VoiceOverlayService.instance?.hideOverlay()
                }
            }

            AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED -> {
                if (event.source == null) {
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
