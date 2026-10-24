package com.lordmuffin.jarvisvoice.wearcommon

import com.lordmuffin.jarvisvoice.wearcommon.sync.DirectUploader
import com.lordmuffin.jarvisvoice.wearcommon.sync.JsonQueueStore
import com.lordmuffin.jarvisvoice.wearcommon.sync.PhoneLink
import com.lordmuffin.jarvisvoice.wearcommon.sync.QueueItem
import com.lordmuffin.jarvisvoice.wearcommon.sync.QueueStore
import com.lordmuffin.jarvisvoice.wearcommon.sync.SyncEnvironment
import com.lordmuffin.jarvisvoice.wearcommon.sync.SyncMachine
import com.lordmuffin.jarvisvoice.wearcommon.sync.SyncRunner
import com.lordmuffin.jarvisvoice.wearcommon.sync.SyncState
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

class SyncRunnerTest {
    @get:Rule val tmp = TemporaryFolder()

    private var now = 1_000_000L
    private lateinit var store: QueueStore
    private val phone = FakePhone()
    private val direct = FakeDirect()
    private val env = FakeEnv()
    private lateinit var runner: SyncRunner

    class FakePhone : PhoneLink {
        var reachable = true
        var sendOk = true
        val sent = mutableListOf<String>()
        override suspend fun isReachable() = reachable
        override suspend fun send(item: QueueItem, file: File): Boolean {
            if (!sendOk) return false
            sent += item.id
            return true
        }
    }

    class FakeDirect : DirectUploader {
        var result: UploadResult = UploadResult.Acked("s-1")
        var throwing = false
        val uploads = mutableListOf<String>()
        override suspend fun upload(item: QueueItem, file: File): UploadResult {
            uploads += item.id
            if (throwing) error("socket exploded")
            return result
        }
    }

    class FakeEnv : SyncEnvironment {
        var network = true
        var configured = true
        override fun networkAvailable() = network
        override fun serverConfigured() = configured
    }

    @Before fun setUp() {
        store = JsonQueueStore(File(tmp.root, "queue.json"))
        runner = SyncRunner(store, phone, direct, env) { now }
    }

    private fun record(id: String = "r1"): File {
        val f = File(tmp.root, "$id.aac").apply { writeBytes(ByteArray(1000) { 7 }) }
        store.put(QueueItem(id, f.absolutePath, startedAtMs = now - 10_000, durationMs = 9_000, bytes = 1000))
        return f
    }

    private fun run() = runBlocking { runner.runOnce() }

    @Test fun `phone path - hand off, keep the audio, delete only when the ack arrives`() {
        val f = record()
        run()
        assertEquals(listOf("r1"), phone.sent)
        assertEquals(SyncState.AT_PHONE, store.get("r1")!!.state)
        assertTrue("audio must stay until the server acks", f.exists())

        run()                                 // nothing new to do, still kept
        assertTrue(f.exists())

        runner.onAck("r1", AckStatus.ACKED)
        assertFalse(f.exists())
        assertNull(store.get("r1"))
    }

    @Test fun `direct path - an acked upload frees the audio`() {
        phone.reachable = false
        val f = record()
        run()
        assertEquals(listOf("r1"), direct.uploads)
        assertFalse(f.exists())
        assertNull(store.get("r1"))
    }

    @Test fun `offline - nothing is lost and the report says it is waiting`() {
        phone.reachable = false; env.network = false
        val f = record()
        val report = run()
        assertTrue(f.exists())
        assertEquals(SyncState.RECORDED, store.get("r1")!!.state)
        assertTrue(report.waitingForConnectivity)
        assertEquals(1, report.remaining)
        assertTrue(phone.sent.isEmpty() && direct.uploads.isEmpty())
    }

    @Test fun `reconnecting later drains the backlog in recording order`() {
        phone.reachable = false; env.network = false
        record("b"); store.put(store.get("b")!!.copy(startedAtMs = 200))
        record("a"); store.put(store.get("a")!!.copy(startedAtMs = 100))
        run()
        env.network = true
        run()
        assertEquals(listOf("a", "b"), direct.uploads)
        assertTrue(store.all().isEmpty())
    }

    @Test fun `a failed direct upload keeps the audio and schedules a retry`() {
        phone.reachable = false
        direct.result = UploadResult.Retry("HTTP 503")
        val f = record()
        val report = run()
        assertTrue(f.exists())
        assertEquals(1, store.get("r1")!!.attempts)
        assertEquals(now + SyncMachine.backoffMs(1), report.wakeAtMs)

        run()                                 // inside the backoff window: no second attempt yet
        assertEquals(1, direct.uploads.size)

        now += SyncMachine.backoffMs(1)
        direct.result = UploadResult.Acked("s-2")
        run()
        assertEquals(2, direct.uploads.size)
        assertFalse(f.exists())
    }

    @Test fun `an exception from the network layer is a retry, not a crash and not a loss`() {
        phone.reachable = false
        direct.throwing = true
        val f = record()
        run()
        assertTrue(f.exists())
        assertEquals(SyncState.RECORDED, store.get("r1")!!.state)
    }

    @Test fun `when the send to the phone fails the watch falls back to uploading itself`() {
        phone.sendOk = false
        val f = record()
        run()
        assertEquals(listOf("r1"), direct.uploads)
        assertFalse(f.exists())
    }

    @Test fun `when the send fails and there is no network, it is queued for later`() {
        phone.sendOk = false; env.network = false
        val f = record()
        val report = run()
        assertTrue(f.exists())
        assertEquals(1, store.get("r1")!!.attempts)
        assertTrue(report.wakeAtMs != null)
    }

    @Test fun `if the phone never acks, the watch uploads directly after the timeout and the phone's duplicate is harmless`() {
        val f = record()
        run()                                 // → AT_PHONE
        now += SyncMachine.PHONE_ACK_TIMEOUT_MS
        run()
        assertEquals(listOf("r1"), direct.uploads)   // same client id as the phone's upload: server dedupes
        assertFalse(f.exists())
    }

    @Test fun `a revoked token blocks the recording but keeps it, and new config unblocks it`() {
        phone.reachable = false
        direct.result = UploadResult.AuthRejected("HTTP 401")
        val f = record()
        run()
        assertEquals(SyncState.BLOCKED, store.get("r1")!!.state)
        assertTrue(f.exists())

        direct.result = UploadResult.Acked("s")
        run()
        assertEquals(1, direct.uploads.size)  // blocked: no hammering the server with a dead token

        runner.onConfigChanged()
        run()
        assertEquals(2, direct.uploads.size)
        assertFalse(f.exists())
    }

    @Test fun `a file the server refuses is kept and quarantined`() {
        phone.reachable = false
        direct.result = UploadResult.Rejected("415")
        val f = record()
        run(); run()
        assertEquals(SyncState.QUARANTINED, store.get("r1")!!.state)
        assertTrue(f.exists())
        assertEquals(1, direct.uploads.size)
    }

    @Test fun `a phone rejection ack quarantines instead of deleting`() {
        val f = record()
        run()
        runner.onAck("r1", AckStatus.REJECTED)
        assertEquals(SyncState.QUARANTINED, store.get("r1")!!.state)
        assertTrue(f.exists())
    }

    @Test fun `an ack for an unknown recording is ignored`() {
        runner.onAck("nope", AckStatus.ACKED)
        assertTrue(store.all().isEmpty())
    }

    @Test fun `a queue entry whose audio vanished is flagged, not dropped`() {
        val f = record()
        f.delete()
        run()
        assertEquals(SyncState.QUARANTINED, store.get("r1")!!.state)
        assertEquals("audio file missing", store.get("r1")!!.lastError)
    }

    @Test fun `a restart picks up exactly where it left off`() {
        phone.reachable = false; env.network = false
        val f = record()
        run()
        // new process: new store object over the same file, new runner
        val store2 = JsonQueueStore(File(tmp.root, "queue.json"))
        env.network = true
        SyncRunner(store2, phone, direct, env) { now }.let { runBlocking { it.runOnce() } }
        assertEquals(listOf("r1"), direct.uploads)
        assertFalse(f.exists())
    }
}
