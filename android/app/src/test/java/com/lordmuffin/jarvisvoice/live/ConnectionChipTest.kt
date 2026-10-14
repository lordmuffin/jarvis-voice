package com.lordmuffin.jarvisvoice.live

import com.lordmuffin.jarvisvoice.live.ConnectionKind.CONNECTED
import com.lordmuffin.jarvisvoice.live.ConnectionKind.CONNECTING
import com.lordmuffin.jarvisvoice.live.ConnectionKind.DEGRADED
import com.lordmuffin.jarvisvoice.live.ConnectionKind.FAILED
import com.lordmuffin.jarvisvoice.live.ConnectionKind.IDLE
import com.lordmuffin.jarvisvoice.live.ConnectionKind.LOCAL_ONLY
import com.lordmuffin.jarvisvoice.live.ConnectionKind.RECONNECTING
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The connection words shared with web and macOS: Connected, Reconnecting, Local only, Degraded, Offline. */
class ConnectionChipTest {

    private fun chip(conn: ConnState, localOnly: Boolean = false, lagMs: Long = 0, llmOk: Boolean = true) =
        ConnectionChip.of(conn, localOnly, lagMs, llmOk)

    @Test fun socketStatesMapToTheSharedWords() {
        assertEquals(IDLE, chip(ConnState.Idle).kind)
        assertEquals(CONNECTING, chip(ConnState.Connecting).kind)
        assertEquals(CONNECTED, chip(ConnState.Connected, lagMs = 840).kind)
        assertEquals(RECONNECTING, chip(ConnState.Reconnecting(3)).kind)
        assertEquals(IDLE, chip(ConnState.Ended).kind)
    }

    @Test fun failureKeepsItsReason() {
        val failed = chip(ConnState.Failed("session ended"))
        assertEquals(FAILED, failed.kind)
        assertEquals("session ended", failed.detail)
    }

    @Test fun reconnectingKeepsTheAttempt() {
        assertEquals("3", chip(ConnState.Reconnecting(3)).detail)
    }

    /** Local only is the user's choice, never a warning, and it hides whatever the socket is doing. */
    @Test fun localOnlyWinsOverTheSocket() {
        assertEquals(LOCAL_ONLY, chip(ConnState.Connected, localOnly = true).kind)
        assertEquals(LOCAL_ONLY, chip(ConnState.Reconnecting(1), localOnly = true).kind)
        assertEquals(LOCAL_ONLY, chip(ConnState.Idle, localOnly = true).kind)
    }

    @Test fun slowServerIsDegradedAtTwoSeconds() {
        assertEquals(CONNECTED, chip(ConnState.Connected, lagMs = 1_999).kind)
        assertEquals(DEGRADED, chip(ConnState.Connected, lagMs = 2_000).kind)
        assertEquals(DEGRADED, chip(ConnState.Connected, lagMs = 4_200).kind)
    }

    @Test fun lagOnlyMattersWhileConnected() {
        assertEquals(RECONNECTING, chip(ConnState.Reconnecting(1), lagMs = 9_000).kind)
    }

    @Test fun copilotOfflineIsFlaggedWhileConnected() {
        assertTrue(chip(ConnState.Connected, llmOk = false).copilotOffline)
        assertTrue(chip(ConnState.Connected, lagMs = 3_000, llmOk = false).copilotOffline)
        assertFalse(chip(ConnState.Connected, llmOk = true).copilotOffline)
        assertFalse(chip(ConnState.Reconnecting(1), llmOk = false).copilotOffline)
        assertFalse(chip(ConnState.Connected, localOnly = true, llmOk = false).copilotOffline)
    }

    /** History rows: a live session whose recorder stopped sending audio reads "Offline". */
    @Test fun liveSessionWithoutAudioIsOffline() {
        assertTrue(ConnectionChip.isOffline(status = "live", streaming = false))
        assertFalse(ConnectionChip.isOffline(status = "live", streaming = true))
        assertFalse(ConnectionChip.isOffline(status = "done", streaming = false))
        assertFalse(ConnectionChip.isOffline(status = "finalizing", streaming = false))
        assertNull(ConnectionChip.of(ConnState.Connected, false, 0, true).detail)
    }
}
