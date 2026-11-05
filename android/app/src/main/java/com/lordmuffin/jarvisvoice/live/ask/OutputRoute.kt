package com.lordmuffin.jarvisvoice.live.ask

import android.content.Context
import android.media.AudioDeviceCallback
import android.media.AudioDeviceInfo
import android.media.AudioManager
import android.os.Build
import android.os.Handler
import android.os.Looper

/**
 * Where a reply would actually play, and a callback when that changes. Reads the route for the
 * speech's own attributes, not a preferred device: a preference falls back to the speaker when
 * headphones go. Before Android 13 the route cannot be read that way, so asks are text only there.
 * [through] is read on every check, so a change in Settings applies to the next reply.
 */
class OutputRoute(
    context: Context,
    private val through: () -> SpeakThrough,
    private val onChange: (blocked: AskUi.Why?) -> Unit,
) {
    private val audio = context.getSystemService(AudioManager::class.java)
    private val callback = object : AudioDeviceCallback() {
        override fun onAudioDevicesAdded(added: Array<out AudioDeviceInfo>?) = onChange(blocked())
        override fun onAudioDevicesRemoved(removed: Array<out AudioDeviceInfo>?) = onChange(blocked())
    }

    fun start() = audio.registerAudioDeviceCallback(callback, Handler(Looper.getMainLooper()))

    fun stop() = audio.unregisterAudioDeviceCallback(callback)

    /** Why a reply may not be spoken now; null when it may. */
    fun blocked(): AskUi.Why? {
        val sdk = Build.VERSION.SDK_INT
        val types = if (sdk >= Build.VERSION_CODES.TIRAMISU) {
            audio.getAudioDevicesForAttributes(TtsAskSpeaker.ATTRIBUTES).map { it.type }
        } else {
            emptyList()
        }
        return OutputRoutes.blocked(through(), types, sdk)
    }
}
