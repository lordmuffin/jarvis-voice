package com.lordmuffin.jarvisvoice.live

import com.lordmuffin.jarvisvoice.live.NetworkPlace.AWAY
import com.lordmuffin.jarvisvoice.live.NetworkPlace.HOME
import com.lordmuffin.jarvisvoice.live.NetworkPlace.NETBIRD
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Away-from-home Local only: NetBird detection and the auto/manual switch. */
class AwayModeTest {

    @Test fun overlayRangeIsNetBirdsDefault() {
        assertTrue(NetBird.isOverlayAddress("100.64.0.1"))
        assertTrue(NetBird.isOverlayAddress("100.127.255.254"))
        assertFalse(NetBird.isOverlayAddress("100.63.255.255"))
        assertFalse(NetBird.isOverlayAddress("100.128.0.1"))
        assertFalse(NetBird.isOverlayAddress("192.168.1.10"))
        assertFalse(NetBird.isOverlayAddress("fe80::1%wlan0"))
        assertFalse(NetBird.isOverlayAddress("100.64.0"))
        assertFalse(NetBird.isOverlayAddress("100.64.0.256"))
    }

    @Test fun netBirdNeedsATunnel() {
        // Carrier-grade NAT hands phones 100.64/10 addresses on cellular.
        val cellular = NetInterface("rmnet0", isTunnel = false, addresses = listOf("100.72.3.4"))
        val wifi = NetInterface("wlan0", isTunnel = false, addresses = listOf("192.168.1.20", "fe80::1"))
        val netbird = NetInterface("tun0", isTunnel = true, addresses = listOf("100.92.10.5"))
        val otherVpn = NetInterface("tun1", isTunnel = true, addresses = listOf("10.8.0.2"))
        assertFalse(NetBird.detect(listOf(cellular, wifi)))
        assertFalse(NetBird.detect(listOf(wifi, otherVpn)))
        assertTrue(NetBird.detect(listOf(cellular, netbird)))
    }

    @Test fun followsTheNetwork() {
        var mode = AutoLocalOnly()
        assertFalse("unknown until the first check", mode.localOnly)
        mode = mode.withPlace(AWAY)
        assertTrue(mode.localOnly)
        mode = mode.withPlace(NETBIRD)
        assertFalse(mode.localOnly)
        mode = mode.withPlace(HOME)
        assertFalse(mode.localOnly)
    }

    @Test fun manualChoiceHoldsUntilTheAutomaticOneChanges() {
        // Away, NetBird running but not seen: stream anyway.
        var mode = AutoLocalOnly().withPlace(AWAY).withManual(false)
        assertFalse(mode.localOnly)
        assertEquals(false, mode.manual)
        // Still away (a re-check after a Wi-Fi hop): the choice stands.
        mode = mode.withPlace(AWAY)
        assertFalse(mode.localOnly)
        // Home: automatic says stream too, and the choice is spent.
        mode = mode.withPlace(HOME)
        assertNull(mode.manual)
        // Leaving again turns Local only back on.
        mode = mode.withPlace(AWAY)
        assertTrue(mode.localOnly)
    }

    @Test fun manualLocalOnlyAtHomeSurvivesHomeToNetBird() {
        var mode = AutoLocalOnly().withPlace(HOME).withManual(true)
        mode = mode.withPlace(NETBIRD)
        assertTrue("both stream automatically, so nothing changed", mode.localOnly)
        mode = mode.withPlace(AWAY)
        assertNull(mode.manual)
        assertTrue(mode.localOnly)
        mode = mode.withPlace(HOME)
        assertFalse(mode.localOnly)
    }

    @Test fun flippingBackToTheAutomaticValueClearsTheOverride() {
        val mode = AutoLocalOnly().withPlace(AWAY).withManual(false).withManual(true)
        assertNull(mode.manual)
        assertTrue(mode.localOnly)
    }

    @Test fun choiceBeforeTheFirstCheckIsMeasuredAgainstIt() {
        val pending = AutoLocalOnly().withManual(false)
        assertNull(pending.manualAgainst)
        // The check finds the phone away: the choice overrides that from now on.
        val away = pending.withPlace(AWAY)
        assertFalse(away.localOnly)
        assertEquals(true, away.manualAgainst)
        // Or it finds home, which the choice agrees with: nothing to override.
        assertNull(pending.withPlace(HOME).manual)
    }

    @Test fun disabledIsAPlainSwitch() {
        var mode = AutoLocalOnly().withPlace(AWAY).withEnabled(false)
        assertTrue("keeps what the switch showed", mode.localOnly)
        mode = mode.withManual(false).withPlace(HOME).withPlace(AWAY)
        assertFalse(mode.localOnly)
        mode = mode.withEnabled(true)
        assertNull(mode.manual)
        assertTrue(mode.localOnly)
    }
}
