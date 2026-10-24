package com.lordmuffin.jarvisvoice.wearcommon

import com.lordmuffin.jarvisvoice.wearcommon.sync.QueueItem
import com.lordmuffin.jarvisvoice.wearcommon.sync.SyncAction
import com.lordmuffin.jarvisvoice.wearcommon.sync.SyncEnv
import com.lordmuffin.jarvisvoice.wearcommon.sync.SyncMachine
import com.lordmuffin.jarvisvoice.wearcommon.sync.SyncState
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class SyncMachineTest {
    private val now = 1_000_000L
    private fun item(state: SyncState = SyncState.RECORDED, block: QueueItem.() -> QueueItem = { this }) =
        QueueItem("r1", "/f/r1.aac", startedAtMs = 5, durationMs = 9_000, bytes = 4_000, state = state).block()

    private val phoneOnly = SyncEnv(phoneReachable = true, networkAvailable = false, serverConfigured = true)
    private val netOnly = SyncEnv(phoneReachable = false, networkAvailable = true, serverConfigured = true)
    private val both = SyncEnv(phoneReachable = true, networkAvailable = true, serverConfigured = true)
    private val offline = SyncEnv(phoneReachable = false, networkAvailable = false, serverConfigured = true)

    // --- routing -----------------------------------------------------------------------------

    @Test fun `a new recording goes to the phone when it is reachable, even if the watch has network`() {
        assertEquals(SyncAction.SendToPhone, SyncMachine.next(item(), now, phoneOnly))
        assertEquals(SyncAction.SendToPhone, SyncMachine.next(item(), now, both))
    }

    @Test fun `with no phone it uploads directly when the watch has network and a server`() {
        assertEquals(SyncAction.UploadDirect, SyncMachine.next(item(), now, netOnly))
    }

    @Test fun `with neither route it waits for connectivity and keeps the recording`() {
        assertEquals(SyncAction.Wait(null), SyncMachine.next(item(), now, offline))
    }

    @Test fun `no server config means no direct upload`() {
        val env = netOnly.copy(serverConfigured = false)
        assertEquals(SyncAction.Wait(null), SyncMachine.next(item(), now, env))
    }

    @Test fun `a failed attempt waits out its backoff before trying again`() {
        val failed = item { copy(attempts = 1, nextAttemptAtMs = now + 60_000) }
        assertEquals(SyncAction.Wait(now + 60_000), SyncMachine.next(failed, now, both))
        assertEquals(SyncAction.SendToPhone, SyncMachine.next(failed, now + 60_000, both))
    }

    // --- phone hand-off ----------------------------------------------------------------------

    @Test fun `once the phone has the file the watch waits for the server ack and does not delete`() {
        val atPhone = SyncMachine.sentToPhone(item(), now)
        assertEquals(SyncState.AT_PHONE, atPhone.state)
        assertEquals(now, atPhone.atPhoneSinceMs)
        assertEquals(SyncAction.Wait(now + SyncMachine.PHONE_ACK_TIMEOUT_MS), SyncMachine.next(atPhone, now + 1, both))
    }

    @Test fun `if the phone never acks, the watch uploads directly after the timeout`() {
        val atPhone = SyncMachine.sentToPhone(item(), now)
        val later = now + SyncMachine.PHONE_ACK_TIMEOUT_MS
        assertEquals(SyncAction.UploadDirect, SyncMachine.next(atPhone, later, both))
    }

    @Test fun `after the timeout with only the phone in reach it sends the file again`() {
        val atPhone = SyncMachine.sentToPhone(item(), now)
        val later = now + SyncMachine.PHONE_ACK_TIMEOUT_MS
        assertEquals(SyncAction.SendToPhone, SyncMachine.next(atPhone, later, phoneOnly))
    }

    @Test fun `after the timeout with no route it keeps waiting`() {
        val atPhone = SyncMachine.sentToPhone(item(), now)
        assertEquals(SyncAction.Wait(null), SyncMachine.next(atPhone, now + SyncMachine.PHONE_ACK_TIMEOUT_MS, offline))
    }

    @Test fun `a failed send to the phone stays recorded and backs off`() {
        val failed = SyncMachine.phoneSendFailed(item(), now, "channel closed")
        assertEquals(SyncState.RECORDED, failed.state)
        assertEquals(1, failed.attempts)
        assertEquals(now + SyncMachine.backoffMs(1), failed.nextAttemptAtMs)
        assertEquals("channel closed", failed.lastError)
    }

    // --- server ack is the only road to deletion ---------------------------------------------

    @Test fun `only an acked recording is deleted`() {
        for (s in listOf(SyncState.RECORDED, SyncState.AT_PHONE, SyncState.BLOCKED, SyncState.QUARANTINED)) {
            assertTrue("$s", SyncMachine.next(item(s), now, both) != SyncAction.DeleteLocal)
        }
        assertEquals(SyncAction.DeleteLocal, SyncMachine.next(item(SyncState.ACKED), now, offline))
    }

    @Test fun `the phone relaying the server ack settles the recording`() {
        val acked = SyncMachine.serverAck(SyncMachine.sentToPhone(item(), now))
        assertEquals(SyncState.ACKED, acked.state)
    }

    @Test fun `a direct upload acked by the server settles the recording`() {
        val acked = SyncMachine.uploadResult(item(), now, UploadResult.Acked("sess-1"))
        assertEquals(SyncState.ACKED, acked.state)
    }

    @Test fun `a direct upload that fails keeps the recording and backs off exponentially up to a cap`() {
        var cur = item()
        val waits = (1..12).map {
            cur = SyncMachine.uploadResult(cur, now, UploadResult.Retry("timeout"))
            cur.nextAttemptAtMs - now
        }
        assertEquals(SyncState.RECORDED, cur.state)
        assertEquals(12, cur.attempts)
        assertEquals(SyncMachine.BACKOFF_BASE_MS, waits.first())
        assertEquals(waits.first() * 2, waits[1])
        assertEquals(SyncMachine.BACKOFF_MAX_MS, waits.last())
        assertTrue(waits.zipWithNext().all { (a, b) -> b >= a })
    }

    @Test fun `a retry from the phone-wait state goes back to recorded`() {
        val atPhone = SyncMachine.sentToPhone(item(), now)
        val r = SyncMachine.uploadResult(atPhone, now + 1, UploadResult.Retry("503"))
        assertEquals(SyncState.RECORDED, r.state)
    }

    // --- terminal-ish states keep the audio --------------------------------------------------

    @Test fun `a rejected token blocks the recording until the config changes`() {
        val blocked = SyncMachine.uploadResult(item(), now, UploadResult.AuthRejected("HTTP 401"))
        assertEquals(SyncState.BLOCKED, blocked.state)
        assertEquals(SyncAction.Wait(null), SyncMachine.next(blocked, now + 10_000_000, both))
        val again = SyncMachine.configChanged(blocked)
        assertEquals(SyncState.RECORDED, again.state)
        assertEquals(0, again.attempts)
        assertEquals(SyncAction.SendToPhone, SyncMachine.next(again, now, both))
    }

    @Test fun `config change leaves other states alone`() {
        val atPhone = SyncMachine.sentToPhone(item(), now)
        assertEquals(atPhone, SyncMachine.configChanged(atPhone))
    }

    @Test fun `a file the server refuses is quarantined, never retried, never deleted`() {
        val q = SyncMachine.uploadResult(item(), now, UploadResult.Rejected("415"))
        assertEquals(SyncState.QUARANTINED, q.state)
        assertEquals(SyncAction.Wait(null), SyncMachine.next(q, now + 10_000_000, both))
        assertEquals(SyncState.QUARANTINED, SyncMachine.phoneRejected(item(SyncState.AT_PHONE), "415").state)
    }

    @Test fun `a server ack settles even a quarantined recording`() {
        val q = item(SyncState.QUARANTINED)
        assertEquals(SyncState.ACKED, SyncMachine.serverAck(q).state)  // the server has it, so it may go
    }
}
