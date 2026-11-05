package com.lordmuffin.jarvisvoice

import android.content.SharedPreferences
import android.media.AudioDeviceInfo

/** An input as Android reports it, without the framework object, so the picker rules run on the JVM. */
data class ReportedInput(val type: Int, val productName: String, val isSource: Boolean = true)

/** One row of the Settings → Microphone picker. [key] is what gets saved. */
data class MicEntry(val key: String, val label: String)

/** What capture opens. [fallback] says why it isn't the saved choice, for DebugLog; null when it is. */
data class MicResolution<T>(val device: T?, val fallback: String?)

/**
 * The microphone choice. A phone reports several built-in mics (a Fold has three) and can report the
 * same headset twice; the picker shows the phone microphone once, first, and every other mic once.
 *
 * The choice is saved as a stable key, [PHONE] or "type:product name", never AudioDeviceInfo.id,
 * which Android renumbers across reboots and reconnects. There is no "let Android choose": on the Fold
 * that opened a mic that did not hear the user, and nothing needs it.
 */
object MicChoice {
    const val PHONE = "builtin"
    const val KEY = "preferred_input_device"
    /** The pre-key setting: an AudioDeviceInfo.id, or -1 for "System Default". */
    const val LEGACY_KEY_ID = "preferred_input_device_id"

    private const val PHONE_LABEL = "Phone microphone"

    /** Inputs a person speaks into. The earpiece, telephony uplink, line-in and the rest stay out. */
    private val MIC_TYPES = setOf(
        AudioDeviceInfo.TYPE_BLUETOOTH_SCO,
        AudioDeviceInfo.TYPE_BLE_HEADSET,
        AudioDeviceInfo.TYPE_WIRED_HEADSET,
        AudioDeviceInfo.TYPE_USB_HEADSET,
        AudioDeviceInfo.TYPE_USB_DEVICE,
    )

    fun entries(inputs: List<ReportedInput>): List<MicEntry> {
        val others = inputs
            .filter { it.isSource && it.type in MIC_TYPES }
            .map { keyOf(it) }
            .distinct()
            .map { MicEntry(it, label(it)) }
        return listOf(MicEntry(PHONE, PHONE_LABEL)) + others
    }

    fun keyOf(input: ReportedInput): String =
        if (input.type == AudioDeviceInfo.TYPE_BUILTIN_MIC) PHONE else "${input.type}:${input.productName}"

    /** The label for a saved key, whether or not that device is connected now. */
    fun label(key: String): String {
        if (key == PHONE) return PHONE_LABEL
        val type = key.substringBefore(':').toIntOrNull()
        val name = key.substringAfter(':', "").ifBlank { null }
        return when (type) {
            AudioDeviceInfo.TYPE_BLUETOOTH_SCO,
            AudioDeviceInfo.TYPE_BLE_HEADSET   -> "${name ?: "Headset"} (Bluetooth)"
            AudioDeviceInfo.TYPE_WIRED_HEADSET -> "Wired headset"
            AudioDeviceInfo.TYPE_USB_HEADSET,
            AudioDeviceInfo.TYPE_USB_DEVICE    -> "${name ?: "Headset"} (USB)"
            else                               -> name ?: "Microphone"
        }
    }

    /** The current device for [key]; if it is gone, the phone microphone, and [MicResolution.fallback] says so. */
    fun <T> resolve(key: String, devices: List<T>, describe: (T) -> ReportedInput): MicResolution<T> {
        val sources = devices.filter { describe(it).isSource }
        val phone = sources.firstOrNull { describe(it).type == AudioDeviceInfo.TYPE_BUILTIN_MIC }
        if (key != PHONE) {
            sources.firstOrNull { describe(it).type in MIC_TYPES && keyOf(describe(it)) == key }
                ?.let { return MicResolution(it, null) }
        }
        val why = when {
            phone == null -> "no built-in mic reported; Android picks the input"
            key == PHONE  -> return MicResolution(phone, null)
            else          -> "${label(key)} not connected; using the phone microphone"
        }
        return MicResolution(phone, why)
    }

    fun saved(prefs: SharedPreferences): String = prefs.getString(KEY, null) ?: PHONE

    fun save(prefs: SharedPreferences, key: String) = prefs.edit().putString(KEY, key).apply()

    /**
     * One-time: whatever the old setting held ("System Default" or a device id), start on the phone
     * microphone, the mic that hears the user. Runs on every start; does nothing once [KEY] is set.
     */
    fun migrate(prefs: SharedPreferences) {
        if (prefs.contains(KEY)) return
        prefs.edit().putString(KEY, PHONE).remove(LEGACY_KEY_ID).apply()
    }
}
