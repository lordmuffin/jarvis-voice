package com.lordmuffin.jarvisvoice

import android.content.Context
import android.media.AudioDeviceInfo
import android.media.AudioManager

class AudioDeviceRouter(private val context: Context) {

    private val audioManager = context.getSystemService(AudioManager::class.java)
    private val prefs = context.getSharedPreferences(VoiceOverlayService.PREF_FILE, Context.MODE_PRIVATE)

    companion object {
        const val KEY_INPUT_DEVICE_ID = "preferred_input_device_id"
        const val DEVICE_DEFAULT = -1
    }

    fun getInputDevices(): List<AudioDeviceInfo> =
        audioManager.getDevices(AudioManager.GET_DEVICES_INPUTS).toList()

    fun getSavedDeviceId(): Int = prefs.getInt(KEY_INPUT_DEVICE_ID, DEVICE_DEFAULT)

    fun saveDeviceId(id: Int) = prefs.edit().putInt(KEY_INPUT_DEVICE_ID, id).apply()

    fun getPreferredDevice(): AudioDeviceInfo? {
        val id = getSavedDeviceId()
        if (id == DEVICE_DEFAULT) return null
        return getInputDevices().find { it.id == id }
    }

    fun deviceLabel(device: AudioDeviceInfo): String = when (device.type) {
        AudioDeviceInfo.TYPE_BUILTIN_MIC      -> "Phone Microphone"
        AudioDeviceInfo.TYPE_BUILTIN_EARPIECE -> "Earpiece"
        AudioDeviceInfo.TYPE_BLUETOOTH_SCO    -> "${device.productName} (Bluetooth)"
        AudioDeviceInfo.TYPE_WIRED_HEADSET    -> "Wired Headset"
        AudioDeviceInfo.TYPE_WIRED_HEADPHONES -> "Wired Headphones"
        AudioDeviceInfo.TYPE_USB_DEVICE,
        AudioDeviceInfo.TYPE_USB_HEADSET      -> "${device.productName} (USB)"
        else                                  -> device.productName.toString()
    }

    fun currentLabel(): String {
        val id = getSavedDeviceId()
        if (id == DEVICE_DEFAULT) return "System Default"
        val device = getInputDevices().find { it.id == id }
        return if (device != null) deviceLabel(device) else "System Default"
    }

    fun isBluetoothSco(device: AudioDeviceInfo) =
        device.type == AudioDeviceInfo.TYPE_BLUETOOTH_SCO

    fun startBluetoothSco() = audioManager.startBluetoothSco()

    fun stopBluetoothSco() = audioManager.stopBluetoothSco()

    /** Blocks up to [timeoutMs] waiting for SCO to connect. Safe to call on a background thread. */
    fun waitForSco(timeoutMs: Long = 2_000L): Boolean {
        val deadline = System.currentTimeMillis() + timeoutMs
        while (System.currentTimeMillis() < deadline) {
            if (audioManager.isBluetoothScoOn) return true
            Thread.sleep(50)
        }
        return audioManager.isBluetoothScoOn
    }
}
