package com.lordmuffin.jarvisvoice.wearcommon

import androidx.test.platform.app.InstrumentationRegistry
import com.lordmuffin.jarvisvoice.wearcommon.inbox.ReceiveResult
import com.lordmuffin.jarvisvoice.wearcommon.inbox.WatchInbox
import com.lordmuffin.jarvisvoice.wearcommon.inbox.WatchUploadProcessor
import com.lordmuffin.jarvisvoice.wearcommon.sync.JsonQueueStore
import com.lordmuffin.jarvisvoice.wearcommon.sync.QueueItem
import com.lordmuffin.jarvisvoice.wearcommon.sync.SyncEnvironment
import com.lordmuffin.jarvisvoice.wearcommon.sync.SyncRunner
import com.lordmuffin.jarvisvoice.wearcommon.sync.SyncState
import com.lordmuffin.jarvisvoice.wearcommon.transfer.NodeTransport
import com.lordmuffin.jarvisvoice.wearcommon.transfer.RecordingSender
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.io.File
import java.io.FilterOutputStream
import java.io.IOException
import java.io.OutputStream
import java.io.PipedInputStream
import java.io.PipedOutputStream
import kotlin.concurrent.thread

/**
 * A stand-in for the paired phone's Data Layer node. The channel is a pipe: the watch writes into
 * it while a receiver thread (what `WearableListenerService.onChannelOpened` does with the real
 * channel's input stream) feeds [WatchInbox], on the device's real file system.
 */
private class FakeNode(private val inbox: WatchInbox) : NodeTransport {
    @Volatile var online = true

    /** Cut the link after this many bytes, like walking out of Bluetooth range. */
    @Volatile var dropAfterBytes: Int? = null
    val results = mutableListOf<ReceiveResult>()

    override suspend fun reachableNodeIds(): List<String> = if (online) listOf("fake-phone") else emptyList()

    override suspend fun openOutput(nodeId: String, path: String): OutputStream {
        if (!online) throw IOException("node unreachable")
        val sink = PipedInputStream(32 * 1024)
        val pipe = PipedOutputStream(sink)
        val receiver = thread(name = "fake-node-receiver") {
            val r = inbox.receive(path, sink)
            synchronized(results) { results += r }
        }
        val limit = dropAfterBytes
        return object : FilterOutputStream(pipe) {
            var written = 0
            override fun write(b: ByteArray, off: Int, len: Int) {
                if (limit != null && written + len > limit) {
                    out.write(b, off, limit - written)
                    close()
                    throw IOException("link lost")
                }
                out.write(b, off, len)
                written += len
            }

            override fun write(b: Int) = write(byteArrayOf(b.toByte()), 0, 1)

            override fun close() {
                runCatching { out.close() }
                receiver.join(5_000)
            }
        }
    }
}

class FakeNodeTransferTest {
    private lateinit var root: File
    private lateinit var inbox: WatchInbox
    private lateinit var node: FakeNode
    private lateinit var store: JsonQueueStore
    private lateinit var runner: SyncRunner
    private val audio = ByteArray(200_000) { (it * 31).toByte() }   // several channel buffers' worth

    private val env = object : SyncEnvironment {
        override fun networkAvailable() = false      // watch has no Wi-Fi/LTE: the phone is the only way out
        override fun serverConfigured() = true
    }

    @Before fun setUp() {
        val ctx = InstrumentationRegistry.getInstrumentation().targetContext
        root = File(ctx.cacheDir, "fake-node-${System.nanoTime()}").also { it.mkdirs() }
        inbox = WatchInbox(File(root, "phone-inbox"))
        node = FakeNode(inbox)
        store = JsonQueueStore(File(root, "watch-queue.json"))
        val direct = com.lordmuffin.jarvisvoice.wearcommon.sync.DirectUploader { _, _ -> error("no network") }
        runner = SyncRunner(store, RecordingSender(node), direct, env)
    }

    @After fun tearDown() { root.deleteRecursively() }

    private fun watchRecording(id: String = "w-1"): File {
        val f = File(root, "$id.aac").apply { writeBytes(audio) }
        store.put(QueueItem(id, f.absolutePath, startedAtMs = 1_700_000_000_000, durationMs = 12_000, bytes = f.length()))
        return f
    }

    @Test fun recordingCrossesToThePhoneIntactAndTheWatchKeepsItUntilTheServerAcks() = runBlocking {
        val f = watchRecording()

        runner.runOnce()

        val got = inbox.pending().single()
        assertEquals("w-1", got.meta.id)
        assertArrayEquals(audio, got.file.readBytes())
        assertEquals(SyncState.AT_PHONE, store.get("w-1")!!.state)
        assertTrue("watch must keep its copy until the server acks", f.exists())

        // the phone uploads; the server answers 2xx; the ack goes back to the watch
        val acks = mutableListOf<Pair<String, AckStatus>>()
        val out = WatchUploadProcessor(inbox, { UploadResult.Acked("sess") }, { id, s -> acks += id to s }).processAll()
        assertEquals(WatchUploadProcessor.Outcome.DONE, out)
        assertTrue(inbox.pending().isEmpty())
        acks.forEach { (id, s) -> runner.onAck(id, s) }

        assertFalse("deleted only now", f.exists())
        assertNull(store.get("w-1"))
    }

    @Test fun aLinkThatDropsMidTransferLeavesNothingHalfwayOnThePhoneAndNothingLostOnTheWatch() = runBlocking {
        val f = watchRecording()
        node.dropAfterBytes = 50_000

        runner.runOnce()

        assertTrue(inbox.pending().isEmpty())
        assertTrue(node.results.single() is ReceiveResult.Incomplete)
        assertTrue(f.exists())
        assertEquals(SyncState.RECORDED, store.get("w-1")!!.state)
        assertEquals(1, store.get("w-1")!!.attempts)

        // back in range: the retry (after backoff) delivers the whole file
        node.dropAfterBytes = null
        val retryAt = runner.runOnce().wakeAtMs!!
        assertEquals(1, store.get("w-1")!!.attempts)                // still inside the backoff window
        SyncRunner(store, RecordingSender(node), { _, _ -> error("no network") }, env, clock = { retryAt }).runOnce()
        assertArrayEquals(audio, inbox.pending().single().file.readBytes())
    }

    @Test fun noNodeInRangeMeansTheRecordingJustWaits() = runBlocking {
        val f = watchRecording()
        node.online = false

        val report = runner.runOnce()

        assertTrue(f.exists())
        assertTrue(report.waitingForConnectivity)
        assertTrue(inbox.pending().isEmpty())
        assertEquals(SyncState.RECORDED, store.get("w-1")!!.state)
    }

    @Test fun sendingTheSameRecordingTwiceDoesNotDuplicateItOnThePhone() = runBlocking {
        watchRecording()
        runner.runOnce()
        // the watch lost track (e.g. app data cleared) and offers it again
        store.put(store.get("w-1")!!.copy(state = SyncState.RECORDED))
        runner.runOnce()

        assertEquals(1, inbox.pending().size)
        assertTrue(node.results.last() is ReceiveResult.AlreadyHave)
    }

    @Test fun aRejectionFromTheServerKeepsTheAudioOnTheWatchForInspection() = runBlocking {
        val f = watchRecording()
        runner.runOnce()
        val acks = mutableListOf<Pair<String, AckStatus>>()
        WatchUploadProcessor(inbox, { UploadResult.Rejected("415") }, { id, s -> acks += id to s }).processAll()
        acks.forEach { (id, s) -> runner.onAck(id, s) }

        assertTrue(f.exists())
        assertEquals(SyncState.QUARANTINED, store.get("w-1")!!.state)
    }
}
