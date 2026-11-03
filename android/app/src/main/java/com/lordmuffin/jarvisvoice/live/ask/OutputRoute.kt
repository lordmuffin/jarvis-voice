package com.lordmuffin.jarvisvoice.live.ask

import android.content.Context
import android.media.AudioAttributes
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
 */
class OutputRoute(context: Context, private val onChange: (closed: Boolean) -> Unit) {
    private val audio = context.getSystemService(AudioManager::class.java)
    private val attributes = AudioAttributes.Builder()
        .setUsage(AudioAttributes.USAGE_MEDIA)
        .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
        .build()
    private val callback = object : AudioDeviceCallback() {
        override fun onAudioDevicesAdded(added: Array<out AudioDeviceInfo>?) = onChange(isClosed())
        override fun onAudioDevicesRemoved(removed: Array<out AudioDeviceInfo>?) = onChange(isClosed())
    }

    fun start() = audio.registerAudioDeviceCallback(callback, Handler(Looper.getMainLooper()))

    fun stop() = audio.unregisterAudioDeviceCallback(callback)

    fun isClosed(): Boolean {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return false
        return OutputRoutes.allClosed(audio.getAudioDevicesForAttributes(attributes).map { it.type })
    }
}
