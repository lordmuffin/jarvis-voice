package com.lordmuffin.jarvisvoice.wearcommon

import com.lordmuffin.jarvisvoice.wearcommon.inbox.ReceiveResult
import com.lordmuffin.jarvisvoice.wearcommon.inbox.WatchInbox
import com.lordmuffin.jarvisvoice.wearcommon.inbox.WatchUploadProcessor
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.ByteArrayInputStream
import java.io.File
import java.io.IOException
import java.io.InputStream

class WatchInboxTest {
    @get:Rule val tmp = TemporaryFolder()
    private val inbox by lazy { WatchInbox(File(tmp.root, "inbox")) }
    private val audio = ByteArray(5000) { (it % 251).toByte() }
    private val meta = RecordingMeta("w-1", 1_700_000_000_000, 12_000, audio.size.toLong())

    @Test fun `a complete transfer is stored byte for byte`() {
        val r = inbox.receive(meta.toPath(), ByteArrayInputStream(audio))
        assertEquals(ReceiveResult.Stored(meta), r)
        val e = inbox.pending().single()
        assertEquals(meta, e.meta)
        assertTrue(audio.contentEquals(e.file.readBytes()))
    }

    @Test fun `a transfer that stops short stores nothing`() {
        val r = inbox.receive(meta.toPath(), ByteArrayInputStream(audio.copyOf(3000)))
        assertEquals(ReceiveResult.Incomplete(5000, 3000), r)
        assertTrue(inbox.pending().isEmpty())
        assertEquals(emptyList<String>(), File(tmp.root, "inbox").list()!!.filter { it.endsWith(".part") })
    }

    @Test fun `a stream that breaks mid-way stores nothing`() {
        val broken = object : InputStream() {
            var n = 0
            override fun read(): Int = if (n++ < 100) 1 else throw IOException("link lost")
        }
        assertTrue(inbox.receive(meta.toPath(), broken) is ReceiveResult.Incomplete)
        assertTrue(inbox.pending().isEmpty())
    }

    @Test fun `a longer-than-declared stream is not accepted either`() {
        assertTrue(inbox.receive(meta.toPath(), ByteArrayInputStream(audio + 1)) is ReceiveResult.Incomplete)
    }

    @Test fun `a bad path is refused`() {
        assertEquals(ReceiveResult.BadPath, inbox.receive("/evil", ByteArrayInputStream(audio)))
    }

    @Test fun `a re-send of a pending or finished recording is recognised`() {
        inbox.receive(meta.toPath(), ByteArrayInputStream(audio))
        assertEquals(ReceiveResult.AlreadyHave(meta, done = false), inbox.receive(meta.toPath(), ByteArrayInputStream(audio)))
        inbox.markDone("w-1")
        assertEquals(ReceiveResult.AlreadyHave(meta, done = true), inbox.receive(meta.toPath(), ByteArrayInputStream(audio)))
        assertTrue(inbox.pending().isEmpty())
    }

    @Test fun `a duplicate is read to the end so the sender's transfer completes`() {
        inbox.receive(meta.toPath(), ByteArrayInputStream(audio))
        val resend = ByteArrayInputStream(audio)
        inbox.receive(meta.toPath(), resend)
        assertEquals("the sender would block on an unread stream", 0, resend.available())
    }

    @Test fun `pending survives a restart`() {
        inbox.receive(meta.toPath(), ByteArrayInputStream(audio))
        assertEquals(1, WatchInbox(File(tmp.root, "inbox")).pending().size)
    }

    // --- uploading what arrived --------------------------------------------------------------

    private fun processor(result: UploadResult, acks: MutableList<Pair<String, AckStatus>>, ackFails: Boolean = false) =
        WatchUploadProcessor(inbox, { result }, { id, s -> if (ackFails) throw IOException("data layer down"); acks += id to s })

    @Test fun `the ack goes to the watch only after the server's 2xx, then the phone's copy goes`() {
        inbox.receive(meta.toPath(), ByteArrayInputStream(audio))
        val acks = mutableListOf<Pair<String, AckStatus>>()
        val out = runBlocking { processor(UploadResult.Retry("503"), acks).processAll() }
        assertEquals(WatchUploadProcessor.Outcome.RETRY_LATER, out)
        assertTrue(acks.isEmpty())
        assertEquals(1, inbox.pending().size)

        val out2 = runBlocking { processor(UploadResult.Acked("s"), acks).processAll() }
        assertEquals(WatchUploadProcessor.Outcome.DONE, out2)
        assertEquals(listOf("w-1" to AckStatus.ACKED), acks)
        assertTrue(inbox.pending().isEmpty())
        assertTrue(inbox.isDone("w-1"))
    }

    @Test fun `if the ack cannot be published the audio stays so the next run acks again`() {
        inbox.receive(meta.toPath(), ByteArrayInputStream(audio))
        val acks = mutableListOf<Pair<String, AckStatus>>()
        runCatching { runBlocking { processor(UploadResult.Acked("s"), acks, ackFails = true).processAll() } }
        assertEquals(1, inbox.pending().size)
        runBlocking { processor(UploadResult.Acked("s"), acks).processAll() }
        assertEquals(listOf("w-1" to AckStatus.ACKED), acks)
    }

    @Test fun `a bad token stops the run and keeps everything`() {
        inbox.receive(meta.toPath(), ByteArrayInputStream(audio))
        val out = runBlocking { processor(UploadResult.AuthRejected("401"), mutableListOf()).processAll() }
        assertEquals(WatchUploadProcessor.Outcome.AUTH_BLOCKED, out)
        assertEquals(1, inbox.pending().size)
    }

    @Test fun `a refused file is reported to the watch and set aside, not deleted`() {
        inbox.receive(meta.toPath(), ByteArrayInputStream(audio))
        val acks = mutableListOf<Pair<String, AckStatus>>()
        runBlocking { processor(UploadResult.Rejected("415"), acks).processAll() }
        assertEquals(listOf("w-1" to AckStatus.REJECTED), acks)
        assertTrue(inbox.pending().isEmpty())
        assertTrue(File(tmp.root, "inbox/quarantine/w-1.aac").exists())
        assertFalse(inbox.isDone("w-1"))
    }
}
