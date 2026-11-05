package com.lordmuffin.jarvisvoice.live.ask

import android.media.AudioDeviceInfo.TYPE_BLE_SPEAKER
import android.media.AudioDeviceInfo.TYPE_BLUETOOTH_A2DP
import android.media.AudioDeviceInfo.TYPE_BUILTIN_SPEAKER
import android.media.AudioDeviceInfo.TYPE_HDMI
import android.media.AudioDeviceInfo.TYPE_WIRED_HEADPHONES
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** Speak answers through: where a held ask's reply may play (docs/plans/voice-push-to-talk.md). */
class SpeakThroughTest {

    private val t = 33  // Android 13, where the route can be read
    private val old = 32

    @Test fun thisDeviceIsTheDefaultForNewInstallsAndUpgrades() {
        assertEquals(SpeakThrough.DEVICE, SpeakThrough.fromKey(null))
        assertEquals(SpeakThrough.DEVICE, SpeakThrough.fromKey("something old"))
        assertEquals(SpeakThrough.HEADPHONES, SpeakThrough.fromKey(SpeakThrough.HEADPHONES.key))
        assertEquals(SpeakThrough.DEVICE, SpeakThrough.fromKey(SpeakThrough.DEVICE.key))
    }

    @Test fun thisDeviceSpeaksOnAnyMediaRouteTheSpeakerIncluded() {
        val d = SpeakThrough.DEVICE
        assertNull(OutputRoutes.blocked(d, listOf(TYPE_BUILTIN_SPEAKER), t))
        assertNull(OutputRoutes.blocked(d, listOf(TYPE_BLUETOOTH_A2DP), t))
        assertNull(OutputRoutes.blocked(d, listOf(TYPE_BLE_SPEAKER, TYPE_HDMI), t))
        assertNull(OutputRoutes.blocked(d, listOf(TYPE_WIRED_HEADPHONES, TYPE_BUILTIN_SPEAKER), t))
    }

    @Test fun thisDeviceIsTextOnlyWhereTheRouteCannotBeRead() {
        val d = SpeakThrough.DEVICE
        assertEquals(AskUi.Why.NO_OUTPUT, OutputRoutes.blocked(d, listOf(TYPE_BUILTIN_SPEAKER), old))
        assertEquals(AskUi.Why.NO_OUTPUT, OutputRoutes.blocked(d, emptyList(), t))
    }

    @Test fun headphonesOnlyIsTodaysRuleUnchanged() {
        val h = SpeakThrough.HEADPHONES
        assertNull(OutputRoutes.blocked(h, listOf(TYPE_BLUETOOTH_A2DP), t))
        assertNull(OutputRoutes.blocked(h, listOf(TYPE_WIRED_HEADPHONES), t))
        assertEquals(AskUi.Why.ROUTE, OutputRoutes.blocked(h, listOf(TYPE_BUILTIN_SPEAKER), t))
        assertEquals(AskUi.Why.ROUTE, OutputRoutes.blocked(h, listOf(TYPE_BLUETOOTH_A2DP, TYPE_BUILTIN_SPEAKER), t))
        assertEquals(AskUi.Why.ROUTE, OutputRoutes.blocked(h, emptyList(), t))
        assertEquals(AskUi.Why.ROUTE, OutputRoutes.blocked(h, listOf(TYPE_BLUETOOTH_A2DP), old))
    }
}
