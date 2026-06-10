package com.lordmuffin.jarvisvoice

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.AccessibilityServiceInfo
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import android.view.accessibility.AccessibilityWindowInfo

class JarvisAccessibilityService : AccessibilityService() {

    // True while an editable field has been focused since the last app switch.
    // Survives transient WINDOWS_CHANGED events during keyboard animation so the
    // pill appears even when VIEW_FOCUSED fires before the IME window is visible.
    private var hasEditableFocus = false

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
                    hasEditableFocus = true
                    VoiceOverlayService.lastFocusedNode = AccessibilityNodeInfo.obtain(node)
                    // Show immediately if keyboard is already up; otherwise WINDOWS_CHANGED
                    // will fire once the IME window appears and trigger showOverlay() then.
                    if (isKeyboardVisible()) {
                        VoiceOverlayService.instance?.showOverlay()
                    }
                } else {
                    hasEditableFocus = false
                    VoiceOverlayService.lastFocusedNode = null
                }
            }

            // API 28+ — fires when any window appears/disappears, including IME.
            // May fire multiple times during keyboard slide-in animation, so we must
            // not clear lastFocusedNode here — it races with TYPE_VIEW_FOCUSED.
            AccessibilityEvent.TYPE_WINDOWS_CHANGED -> {
                if (isKeyboardVisible()) {
                    // Use hasEditableFocus, not lastFocusedNode: the node may have been
                    // captured in VIEW_FOCUSED before the IME window was registered, and
                    // a transient WINDOWS_CHANGED(keyboard=false) during animation would
                    // have cleared lastFocusedNode, preventing the pill from ever showing.
                    if (hasEditableFocus) {
                        VoiceOverlayService.instance?.showOverlay()
                    }
                } else {
                    val service = VoiceOverlayService.instance
                    // Screen-off: IME window disappears but recording must continue.
                    if (service == null || !service.isScreenOff()) {
                        // Keyboard is genuinely gone — keep lastFocusedNode so a long
                        // dictation session can still inject when it finishes.
                    }
                    service?.hideOverlay()
                }
            }

            AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED -> {
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
        // onInterrupt fires on screen-off among other events. hideOverlay() guards
        // against stopping recording when screenOff=true, so safe to call here.
        VoiceOverlayService.instance?.hideOverlay()
    }
}
