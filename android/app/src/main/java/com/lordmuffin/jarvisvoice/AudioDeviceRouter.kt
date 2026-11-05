package com.lordmuffin.jarvisvoice

import android.content.Context
import android.media.AudioDeviceInfo
import android.media.AudioManager

class AudioDeviceRouter(private val context: Context) {

    private val audioManager = context.getSystemService(AudioManager::class.java)
    private val prefs = context.getSharedPreferences(VoiceOverlayService.PREF_FILE, Context.MODE_PRIVATE)

    init {
        MicChoice.migrate(prefs)
    }

    fun getInputDevices(): List<AudioDeviceInfo> =
        audioManager.getDevices(AudioManager.GET_DEVICES_INPUTS).toList()

    /** The picker's rows for what is connected now: the phone microphone first, every other mic once. */
    fun entries(): List<MicEntry> = MicChoice.entries(getInputDevices().map(::reported))

    fun savedKey(): String = MicChoice.saved(prefs)

    fun saveKey(key: String) = MicChoice.save(prefs, key)

    /**
     * The saved choice as a current device, resolved each time capture starts. A device that is gone
     * falls back to the phone microphone, and DebugLog says so; Android never chooses silently.
     */
    fun getPreferredDevice(): AudioDeviceInfo? {
        val r = MicChoice.resolve(savedKey(), getInputDevices(), ::reported)
        r.fallback?.let { DebugLog.w(TAG, it) }
        return r.device
    }

    /** The phone's own microphone: no Bluetooth route, so nothing for a call to take over. */
    fun builtInMic(): AudioDeviceInfo? =
        getInputDevices().firstOrNull { it.type == AudioDeviceInfo.TYPE_BUILTIN_MIC }

    fun deviceLabel(device: AudioDeviceInfo): String = MicChoice.label(MicChoice.keyOf(reported(device)))

    fun currentLabel(): String = MicChoice.label(savedKey())

    fun isBluetoothSco(device: AudioDeviceInfo) =
        device.type == AudioDeviceInfo.TYPE_BLUETOOTH_SCO

    private fun reported(d: AudioDeviceInfo) = ReportedInput(d.type, d.productName.toString(), d.isSource)

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

    private companion object {
        const val TAG = "Mic"
    }
}
