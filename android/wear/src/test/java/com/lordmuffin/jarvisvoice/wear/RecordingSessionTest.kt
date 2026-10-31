package com.lordmuffin.jarvisvoice.wear

import com.lordmuffin.jarvisvoice.wearcommon.UploadResult
import com.lordmuffin.jarvisvoice.wearcommon.sync.DirectUploader
import com.lordmuffin.jarvisvoice.wearcommon.sync.JsonQueueStore
import com.lordmuffin.jarvisvoice.wearcommon.sync.PhoneLink
import com.lordmuffin.jarvisvoice.wearcommon.sync.QueueItem
import com.lordmuffin.jarvisvoice.wearcommon.sync.QueueStore
import com.lordmuffin.jarvisvoice.wearcommon.sync.RecordingCatalog
import com.lordmuffin.jarvisvoice.wearcommon.sync.SyncEnvironment
import com.lordmuffin.jarvisvoice.wearcommon.sync.SyncRunner
import kotlinx.coroutines.test.runTest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

/** A real recording's order of events, with the microphone replaced and everything else real. */
class RecordingSessionTest {
    @get:Rule val tmp = TemporaryFolder()

    private lateinit var dir: File
    private lateinit var store: JsonQueueStore
    private var elapsed = 10_000L
    private var wall = 1_000_000L

    /** Writes to a real file. [whileStarting] runs inside start(), when the file already exists. */
    private inner class FakeCapture(val whileStarting: (File) -> Unit = {}) : AudioCapture {
        var current: File? = null
        var maxDurationMs = 0
        var startError: Exception? = null
        lateinit var onMaxDuration: () -> Unit
        lateinit var onError: () -> Unit

        override val isRecording get() = current != null
        override val bytesWritten get() = current?.length() ?: 0

        override fun start(id: String, maxDurationMs: Int, onMaxDuration: () -> Unit, onError: () -> Unit): File {
            startError?.let { throw it }
            this.maxDurationMs = maxDurationMs
            this.onMaxDuration = onMaxDuration
            this.onError = onError
            val f = File(dir, "$id.aac").apply { createNewFile() }
            current = f
            whileStarting(f)
            return f
        }

        fun speak(bytes: Int) = current!!.appendBytes(ByteArray(bytes))

        override fun stop(): File? = current.also { current = null }
    }

    private fun session(capture: AudioCapture, q: QueueStore = store, onChanged: (Boolean) -> Unit = {}) =
        RecordingSession(capture, q, { wall }, { elapsed }, onChanged)

    /** What SyncWorker does, against the real queue and runner. */
    private suspend fun syncPass(sent: MutableList<Long>) {
        RecordingCatalog.recoverOrphans(store, dir, RecorderState::isActive, wall)
        SyncRunner(
            store = store,
            phone = object : PhoneLink {
                override suspend fun isReachable() = true
                override suspend fun send(item: QueueItem, file: File): Boolean { sent += file.length(); return true }
            },
            direct = DirectUploader { _, _ -> UploadResult.Retry("unused") },
            environment = object : SyncEnvironment {
                override fun networkAvailable() = false
                override fun serverConfigured() = false
            },
            clock = { wall },
        ).runOnce()
    }

    @Before fun setUp() {
        dir = tmp.newFolder("recordings")
        store = JsonQueueStore(File(tmp.root, "queue.json"))
        RecorderState.stopped()
        RecorderState.clearFailure()
    }

    @After fun tearDown() = RecorderState.stopped()

    // --- Recordings are not empty or missing ---

    @Test fun `a sync pass while the microphone opens leaves the empty new file alone`() = runTest {
        val sent = mutableListOf<Long>()
        val capture = FakeCapture { kotlinx.coroutines.runBlocking { syncPass(sent) } }
        val s = session(capture)

        assertTrue(s.start {})
        assertTrue("the file was deleted under the recorder", capture.current!!.exists())
        capture.speak(5_000)
        elapsed += 60_000
        s.stop()

        assertEquals(5_000L, store.all().single().bytes)
    }

    @Test fun `the file is only created once the recording counts as active`() {
        var activeWhileStarting = false
        val s = session(FakeCapture { activeWhileStarting = RecorderState.isActive(it) })
        s.start {}
        assertTrue(activeWhileStarting)
    }

    // --- Recordings are not limited to a few seconds ---

    @Test fun `a sync pass during a recording never sends part of it`() = runTest {
        val sent = mutableListOf<Long>()
        // Audio is already reaching the file when the sync runs, before start() returned.
        val capture = FakeCapture {
            it.writeBytes(ByteArray(300))
            kotlinx.coroutines.runBlocking { syncPass(sent) }
        }
        val s = session(capture)

        s.start {}
        capture.speak(700)
        syncPass(sent)
        assertTrue("sent early: $sent", sent.isEmpty())
        assertTrue("still recording must keep its file", capture.current!!.exists())

        capture.speak(9_000)
        elapsed += 300_000
        s.stop()
        syncPass(sent)

        assertEquals("the whole file goes out, once", listOf(10_000L), sent)
    }

    @Test fun `the queue has the finished recording before it stops counting as active`() {
        var activeDuringPut = false
        val spy = object : QueueStore by store {
            override fun put(item: QueueItem) {
                activeDuringPut = RecorderState.active.value != null
                store.put(item)
            }
        }
        val capture = FakeCapture()
        val s = session(capture, spy)
        s.start {}
        capture.speak(100)
        elapsed += 5_000
        s.stop()
        assertTrue(activeDuringPut)
    }

    @Test fun `the only limit on a recording is the two hour cap`() {
        val capture = FakeCapture()
        session(capture).start {}
        assertEquals(2 * 60 * 60 * 1000, capture.maxDurationMs)
    }

    @Test fun `hitting the cap ends the recording and queues it whole`() {
        val capture = FakeCapture()
        val s = session(capture)
        s.start { s.stop(it) }
        capture.speak(21_000_000)
        elapsed += RecordingSession.MAX_RECORDING_MS
        capture.onMaxDuration()

        assertFalse(s.isRecording)
        assertEquals(21_000_000L, store.all().single().bytes)
        assertEquals(RecordingSession.MAX_RECORDING_MS.toLong(), store.all().single().durationMs)
    }

    // --- A recording that goes wrong keeps its audio and says so ---

    @Test fun `a recorder error ends the recording, keeps what was captured and reports it`() {
        val capture = FakeCapture()
        val s = session(capture)
        s.start { s.stop(it) }
        capture.speak(4_000)
        elapsed += 8_000

        capture.onError()

        assertFalse("the timer must not keep running over a dead recorder", s.isRecording)
        assertNull(RecorderState.active.value)
        assertEquals(4_000L, store.all().single().bytes)
        assertEquals(RecorderState.Failure.STOPPED_EARLY, RecorderState.failure.value)
    }

    @Test fun `a microphone that will not open reports it and leaves nothing behind`() {
        val capture = FakeCapture().apply { startError = IllegalStateException("start failed") }
        val s = session(capture)

        assertFalse(s.start {})

        assertNull(RecorderState.active.value)
        assertTrue(dir.listFiles().orEmpty().isEmpty())
        assertEquals(RecorderState.Failure.MIC_UNAVAILABLE, RecorderState.failure.value)
    }

    @Test fun `a long recording that captured nothing is reported, not silently dropped`() {
        val capture = FakeCapture()
        val s = session(capture)
        s.start {}
        elapsed += 30_000
        val f = capture.current!!

        s.stop()

        assertTrue(store.all().isEmpty())
        assertFalse(f.exists())
        assertEquals(RecorderState.Failure.NOTHING_CAPTURED, RecorderState.failure.value)
    }

    @Test fun `a recording that stopped growing is caught after two checks`() {
        val capture = FakeCapture()
        val s = session(capture)
        s.start {}
        capture.speak(100)
        assertFalse(s.stalled())
        assertFalse("one quiet check is not enough", s.stalled())
        assertTrue(s.stalled())
    }

    @Test fun `a recording that keeps growing is never called stalled`() {
        val capture = FakeCapture()
        val s = session(capture)
        s.start {}
        repeat(720) {   // two hours of checks
            capture.speak(1)
            assertFalse(s.stalled())
        }
    }

    @Test fun `each recording starts its progress check afresh`() {
        val capture = FakeCapture()
        val s = session(capture)
        s.start {}
        capture.speak(100)
        s.stalled(); s.stalled()
        elapsed += 5_000
        s.stop()
        wall += 1
        s.start {}
        capture.speak(1)
        assertFalse(s.stalled())
    }

    @Test fun `the next recording clears the last failure`() {
        RecorderState.failed(RecorderState.Failure.STOPPED_EARLY)
        session(FakeCapture()).start {}
        assertNull(RecorderState.failure.value)
    }

    @Test fun `a queue that cannot be written keeps the audio for recovery to find`() {
        val failing = object : QueueStore by store {
            override fun put(item: QueueItem) = throw java.io.IOException("disk full")
        }
        val capture = FakeCapture()
        val s = session(capture, failing)
        s.start {}
        capture.speak(500)
        elapsed += 5_000
        val f = capture.current!!

        s.stop()

        assertTrue(f.exists())
        assertNull(RecorderState.active.value)
        assertEquals(listOf(f.nameWithoutExtension), RecordingCatalog.recoverOrphans(store, dir, RecorderState::isActive, wall))
    }

    // --- Nothing around the recording can take it down ---

    @Test fun `a failing tile or haptic refresh does not end the recording`() {
        val capture = FakeCapture()
        val s = session(capture, onChanged = { error("tile service unavailable") })

        assertTrue(s.start {})
        assertTrue(s.isRecording)
        capture.speak(500)
        elapsed += 5_000
        s.stop()

        assertEquals(1, store.all().size)
    }

    @Test fun `the surfaces hear about the start and the stop, after the state changed`() {
        val seen = mutableListOf<Pair<Boolean, Boolean>>()
        val capture = FakeCapture()
        val s = session(capture, onChanged = { seen += it to (RecorderState.active.value != null) })
        s.start {}
        capture.speak(500)
        elapsed += 5_000
        s.stop()
        assertEquals(listOf(true to true, false to false), seen)
    }

    // --- Mis-taps stay quiet ---

    @Test fun `an accidental double tap keeps nothing`() {
        val capture = FakeCapture()
        val s = session(capture)
        s.start {}
        capture.speak(50)
        elapsed += 300
        val f = capture.current!!
        s.stop()
        assertTrue(store.all().isEmpty())
        assertFalse(f.exists())
        assertNull("a mis-tap is not a failure", RecorderState.failure.value)
    }

    @Test fun `stopping with nothing running does nothing`() {
        session(FakeCapture()).stop()
        assertTrue(store.all().isEmpty())
        assertNull(RecorderState.active.value)
    }
}
