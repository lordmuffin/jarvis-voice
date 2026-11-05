package com.lordmuffin.jarvisvoice

import android.content.SharedPreferences
import android.media.AudioDeviceInfo
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Settings → Microphone: what the picker lists, what it saves, and what capture opens. */
class MicChoiceTest {

    private val buds = ReportedInput(AudioDeviceInfo.TYPE_BLUETOOTH_SCO, "Pixel Buds Pro")

    /** What a Galaxy Fold reports with earbuds connected: duplicates, a telephony mic, an earpiece. */
    private val foldInputs = listOf(
        ReportedInput(AudioDeviceInfo.TYPE_BUILTIN_MIC, "SM-F956U"),
        ReportedInput(AudioDeviceInfo.TYPE_BUILTIN_MIC, "SM-F956U"),
        ReportedInput(AudioDeviceInfo.TYPE_BUILTIN_MIC, "SM-F956U"),
        ReportedInput(AudioDeviceInfo.TYPE_TELEPHONY, ""),
        buds,
        buds,
        ReportedInput(AudioDeviceInfo.TYPE_BUILTIN_EARPIECE, "SM-F956U"),
        ReportedInput(AudioDeviceInfo.TYPE_WIRED_HEADPHONES, "SM-F956U"),
    )

    @Test fun foldInputsListOnePhoneMicrophoneFirstAndTheBudsOnce() {
        assertEquals(
            listOf(
                MicEntry(MicChoice.PHONE, "Phone microphone"),
                MicEntry("${AudioDeviceInfo.TYPE_BLUETOOTH_SCO}:Pixel Buds Pro", "Pixel Buds Pro (Bluetooth)"),
            ),
            MicChoice.entries(foldInputs),
        )
    }

    @Test fun phoneMicrophoneIsListedEvenWhenNothingElseIs() {
        val inputs = listOf(ReportedInput(AudioDeviceInfo.TYPE_BUILTIN_MIC, "SM-F956U"))
        assertEquals(listOf(MicEntry(MicChoice.PHONE, "Phone microphone")), MicChoice.entries(inputs))
    }

    @Test fun wiredAndUsbHeadsetsAreListedByTypeAndName() {
        val inputs = listOf(
            ReportedInput(AudioDeviceInfo.TYPE_USB_HEADSET, "Jabra Evolve"),
            ReportedInput(AudioDeviceInfo.TYPE_WIRED_HEADSET, "SM-F956U"),
            ReportedInput(AudioDeviceInfo.TYPE_BUILTIN_MIC, "SM-F956U"),
        )
        assertEquals(
            listOf("Phone microphone", "Jabra Evolve (USB)", "Wired headset"),
            MicChoice.entries(inputs).map { it.label },
        )
    }

    @Test fun anInputThatIsNotASourceIsLeftOut() {
        val inputs = listOf(
            ReportedInput(AudioDeviceInfo.TYPE_BUILTIN_MIC, "SM-F956U"),
            buds.copy(isSource = false),
        )
        assertEquals(listOf(MicChoice.PHONE), MicChoice.entries(inputs).map { it.key })
    }

    @Test fun aSavedKeyNamesItsDeviceWhetherOrNotItIsConnected() {
        assertEquals("Phone microphone", MicChoice.label(MicChoice.PHONE))
        assertEquals("Pixel Buds Pro (Bluetooth)", MicChoice.label(MicChoice.keyOf(buds)))
    }

    @Test fun thePhoneMicrophoneResolvesToABuiltInMic() {
        val r = MicChoice.resolve(MicChoice.PHONE, foldInputs) { it }
        assertEquals(AudioDeviceInfo.TYPE_BUILTIN_MIC, r.device?.type)
        assertNull(r.fallback)
    }

    @Test fun aConnectedDeviceResolvesToItself() {
        val r = MicChoice.resolve(MicChoice.keyOf(buds), foldInputs) { it }
        assertEquals(buds, r.device)
        assertNull(r.fallback)
    }

    @Test fun aGoneDeviceFallsBackToThePhoneMicrophoneAndSaysSo() {
        val phoneOnly = foldInputs.filter { it != buds }
        val r = MicChoice.resolve(MicChoice.keyOf(buds), phoneOnly) { it }
        assertEquals(AudioDeviceInfo.TYPE_BUILTIN_MIC, r.device?.type)
        assertTrue(r.fallback!!.contains("Pixel Buds Pro (Bluetooth)"))
        assertTrue(r.fallback!!.contains("phone microphone"))
    }

    @Test fun noBuiltInMicAtAllIsSaidRatherThanSilentlyLeftToAndroid() {
        val r = MicChoice.resolve(MicChoice.PHONE, listOf(buds)) { it }
        assertNull(r.device)
        assertTrue(r.fallback!!.contains("Android"))
    }

    @Test fun newInstallStartsOnThePhoneMicrophone() {
        val prefs = FakePrefs()
        MicChoice.migrate(prefs)
        assertEquals(MicChoice.PHONE, MicChoice.saved(prefs))
    }

    @Test fun savedSystemDefaultMigratesToThePhoneMicrophone() {
        val prefs = FakePrefs(mutableMapOf(MicChoice.LEGACY_KEY_ID to -1))
        MicChoice.migrate(prefs)
        assertEquals(MicChoice.PHONE, MicChoice.saved(prefs))
        assertFalse(prefs.contains(MicChoice.LEGACY_KEY_ID))
    }

    @Test fun savedDeviceIdMigratesToThePhoneMicrophone() {
        val prefs = FakePrefs(mutableMapOf(MicChoice.LEGACY_KEY_ID to 42))
        MicChoice.migrate(prefs)
        assertEquals(MicChoice.PHONE, MicChoice.saved(prefs))
        assertFalse(prefs.contains(MicChoice.LEGACY_KEY_ID))
    }

    @Test fun migrationRunsOnceAndKeepsALaterChoice() {
        val prefs = FakePrefs()
        MicChoice.migrate(prefs)
        MicChoice.save(prefs, MicChoice.keyOf(buds))
        MicChoice.migrate(prefs)
        assertEquals(MicChoice.keyOf(buds), MicChoice.saved(prefs))
    }
}

/** Just enough SharedPreferences to run the migration on the JVM. */
private class FakePrefs(private val values: MutableMap<String, Any?> = mutableMapOf()) : SharedPreferences {
    override fun getAll(): MutableMap<String, *> = values
    override fun getString(key: String, defValue: String?) = values[key] as String? ?: defValue
    override fun getStringSet(key: String, defValues: MutableSet<String>?) = defValues
    override fun getInt(key: String, defValue: Int) = values[key] as Int? ?: defValue
    override fun getLong(key: String, defValue: Long) = values[key] as Long? ?: defValue
    override fun getFloat(key: String, defValue: Float) = values[key] as Float? ?: defValue
    override fun getBoolean(key: String, defValue: Boolean) = values[key] as Boolean? ?: defValue
    override fun contains(key: String) = values.containsKey(key)
    override fun registerOnSharedPreferenceChangeListener(l: SharedPreferences.OnSharedPreferenceChangeListener?) = Unit
    override fun unregisterOnSharedPreferenceChangeListener(l: SharedPreferences.OnSharedPreferenceChangeListener?) = Unit
    override fun edit(): SharedPreferences.Editor = object : SharedPreferences.Editor {
        private val puts = mutableMapOf<String, Any?>()
        private val removes = mutableSetOf<String>()
        override fun putString(key: String, value: String?) = apply { puts[key] = value }
        override fun putStringSet(key: String, values: MutableSet<String>?) = apply { puts[key] = values }
        override fun putInt(key: String, value: Int) = apply { puts[key] = value }
        override fun putLong(key: String, value: Long) = apply { puts[key] = value }
        override fun putFloat(key: String, value: Float) = apply { puts[key] = value }
        override fun putBoolean(key: String, value: Boolean) = apply { puts[key] = value }
        override fun remove(key: String) = apply { removes += key }
        override fun clear() = apply { removes += values.keys }
        override fun commit(): Boolean { apply(); return true }
        override fun apply() {
            removes.forEach { values.remove(it) }
            values.putAll(puts)
        }
    }
}
