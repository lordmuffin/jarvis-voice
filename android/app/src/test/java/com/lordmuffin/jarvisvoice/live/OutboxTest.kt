package com.lordmuffin.jarvisvoice.live

import com.lordmuffin.jarvisvoice.live.protocol.AudioFrame
import com.lordmuffin.jarvisvoice.live.protocol.Channel
import com.lordmuffin.jarvisvoice.live.protocol.ChannelSeqs
import com.lordmuffin.jarvisvoice.live.protocol.SessionMode
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

class OutboxTest {
    @get:Rule val tmp = TemporaryFolder()

    private fun payload(fill: Int, size: Int = AudioFrame.FRAME_BYTES) = ByteArray(size) { fill.toByte() }

    @Test fun appendsAndReturnsUnackedInOrder() {
        val outbox = Outbox.open(tmp.root, "s1")
        repeat(5) { outbox.append(Channel.MIC, payload(it), it * 100L) }
        assertEquals(5L, outbox.nextSeq(Channel.MIC))
        assertEquals(500, outbox.unackedDurationMs(Channel.MIC))

        val frames = outbox.unacked(Channel.MIC, from = 2, limit = 2)
        assertEquals(listOf(2L, 3L), frames.map { it.seq })
        assertEquals(200L, frames[0].tMs)
        assertArrayEquals(payload(2), frames[0].payload)
        outbox.close()
    }

    @Test fun ackFreesAndPersistsAcrossReopen() {
        val outbox = Outbox.open(tmp.root, "s1")
        repeat(4) { outbox.append(Channel.MIC, payload(it), it * 100L) }
        outbox.markAcked(Channel.MIC, 1)
        outbox.markAcked(Channel.MIC, 0)  // lower acks are ignored
        assertEquals(1L, outbox.ackedSeq(Channel.MIC))
        assertEquals(200, outbox.unackedDurationMs(Channel.MIC))
        assertEquals(200, outbox.unackedDurationMs(Channel.MIC, through = 3))
        assertEquals(100, outbox.unackedDurationMs(Channel.MIC, through = 2))
        outbox.close()

        val reopened = Outbox.reopen(tmp.root, "s1")
        assertEquals(ChannelSeqs(mic = 1, system = null), reopened.ackedSeqs())
        assertEquals(listOf(2L, 3L), reopened.unacked(Channel.MIC).map { it.seq })
        assertEquals(4L, reopened.nextSeq(Channel.MIC))
        reopened.close()
    }

    @Test fun rejectsOutOfOrderSeqAndBadSize() {
        val outbox = Outbox.open(tmp.root, "s1")
        assertThrows(OutboxException::class.java) { outbox.append(AudioFrame(Channel.MIC, 3, 0, payload(0))) }
        assertThrows(OutboxException::class.java) { outbox.append(Channel.MIC, ByteArray(10), 0) }
        outbox.close()
    }

    @Test fun recoversFromTornIndexWrite() {
        val outbox = Outbox.open(tmp.root, "s1")
        repeat(3) { outbox.append(Channel.MIC, payload(it), it * 100L) }
        outbox.close()

        // A crash mid-append: a partial index line and pcm bytes that were never indexed.
        val dir = File(tmp.root, "s1")
        File(dir, "mic.index.jsonl").appendText("""{"seq":3,"t_ms":300,"off""")
        File(dir, "mic.pcm").appendBytes(ByteArray(1000))

        val reopened = Outbox.reopen(tmp.root, "s1")
        assertEquals(3L, reopened.nextSeq(Channel.MIC))
        assertEquals(3L * AudioFrame.FRAME_BYTES, File(dir, "mic.pcm").length())
        reopened.append(Channel.MIC, payload(9), 300)
        assertArrayEquals(payload(9), reopened.unacked(Channel.MIC, from = 3).single().payload)
        reopened.close()
    }

    @Test fun recoversWhenIndexPointsPastPcm() {
        val outbox = Outbox.open(tmp.root, "s1")
        repeat(3) { outbox.append(Channel.MIC, payload(it), it * 100L) }
        outbox.close()
        val pcm = File(tmp.root, "s1/mic.pcm")
        pcm.writeBytes(pcm.readBytes().copyOf(AudioFrame.FRAME_BYTES * 2 + 5))

        val reopened = Outbox.reopen(tmp.root, "s1")
        assertEquals(2L, reopened.nextSeq(Channel.MIC))
        reopened.close()
    }

    @Test fun compactsOnceFullyAckedPastThreshold() {
        val outbox = Outbox.open(tmp.root, "s1")
        val frames = (Outbox.COMPACTION_THRESHOLD_BYTES / AudioFrame.MAX_PAYLOAD + 1).toInt()
        repeat(frames) { outbox.append(Channel.MIC, payload(1, AudioFrame.MAX_PAYLOAD), it * 200L) }
        outbox.markAcked(Channel.MIC, frames - 1L)
        assertEquals(0L, File(tmp.root, "s1/mic.pcm").length())
        assertEquals(frames.toLong(), outbox.nextSeq(Channel.MIC))
        outbox.append(Channel.MIC, payload(2), 0)  // seq continues after compaction
        assertEquals(listOf(frames.toLong()), outbox.unacked(Channel.MIC).map { it.seq })
        outbox.close()
    }

    @Test fun sessionRepositoryRoundTripsAndRenames() {
        val repo = SessionRepository(tmp.root)
        val meta = SessionMeta(
            id = "local-1", mode = SessionMode.MEETING, title = "Standup", startedAt = 1_700_000_000_000,
            localOnly = true, serverId = null, state = SessionMeta.State.PENDING, notePath = "/x.md",
        )
        repo.write(meta)
        Outbox.open(tmp.root, "local-1").apply { append(Channel.MIC, payload(1), 0) }.close()
        assertEquals(meta, repo.read("local-1"))

        val renamed = repo.rename(meta, "3f2b8c1e-5d4a-4b7e-9a10-2c6d8e9f0a1b")
        assertNull(repo.read("local-1"))
        assertEquals(renamed, repo.read(renamed.id))
        assertEquals(1, Outbox.reopen(tmp.root, renamed.id).unacked(Channel.MIC).size)

        repo.write(renamed.copy(state = SessionMeta.State.DONE))
        assertTrue(repo.pending(activeId = null).isEmpty())
    }
}
