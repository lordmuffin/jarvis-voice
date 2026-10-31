package com.lordmuffin.jarvisvoice.wearcommon

import com.lordmuffin.jarvisvoice.wearcommon.sync.JsonQueueStore
import com.lordmuffin.jarvisvoice.wearcommon.sync.QueueItem
import com.lordmuffin.jarvisvoice.wearcommon.sync.RecordingCatalog
import com.lordmuffin.jarvisvoice.wearcommon.sync.SyncState
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

class QueueStoreTest {
    @get:Rule val tmp = TemporaryFolder()
    private val qf get() = File(tmp.root, "queue.json")

    private fun item(id: String, started: Long = 1) = QueueItem(id, "/x/$id.aac", started, 5_000, 900)

    @Test fun `items round trip through the file`() {
        val full = item("a").copy(state = SyncState.AT_PHONE, attempts = 3, nextAttemptAtMs = 77, atPhoneSinceMs = 55, lastError = "boom")
        JsonQueueStore(qf).put(full)
        JsonQueueStore(qf).let {
            assertEquals(listOf(full), it.all())
            assertEquals(full, it.get("a"))
        }
    }

    @Test fun `put replaces by id and remove deletes`() {
        val s = JsonQueueStore(qf)
        s.put(item("a")); s.put(item("b", 2)); s.put(item("a").copy(attempts = 4))
        assertEquals(listOf("a", "b"), s.all().map { it.id })
        assertEquals(4, s.get("a")!!.attempts)
        s.remove("a")
        assertNull(s.get("a"))
    }

    @Test fun `a corrupt queue is set aside, not overwritten, and reads as empty`() {
        qf.writeText("{ not json")
        val s = JsonQueueStore(qf)
        assertTrue(s.all().isEmpty())
        assertTrue(File(qf.path + ".corrupt").exists())
        s.put(item("a"))
        assertEquals(1, s.all().size)
    }

    @Test fun `a torn temp file from a crash does not hurt the committed queue`() {
        JsonQueueStore(qf).put(item("a"))
        File(qf.path + ".tmp").writeText("[{\"id\":")      // died mid-write
        assertEquals(listOf("a"), JsonQueueStore(qf).all().map { it.id })
    }

    @Test fun `recovery queues audio the queue does not know about but leaves the active file alone`() {
        val rec = tmp.newFolder("rec")
        val s = JsonQueueStore(qf)
        val known = File(rec, "w-1.aac").apply { writeBytes(ByteArray(10)) }
        s.put(item("w-1"))
        val orphan = File(rec, "w-2.aac").apply { writeBytes(ByteArray(20)) }
        val active = File(rec, "w-3.aac").apply { writeBytes(ByteArray(5)) }
        val empty = File(rec, "w-4.aac").apply { writeBytes(ByteArray(0)) }
        File(rec, "notes.txt").writeText("x")

        val queued = RecordingCatalog.recoverOrphans(s, rec, { it == active }, now = 999)

        assertEquals(listOf("w-2"), queued)
        assertEquals(20L, s.get("w-2")!!.bytes)
        assertEquals(SyncState.RECORDED, s.get("w-2")!!.state)
        assertNull(s.get("w-3"))
        assertFalse(empty.exists())
        assertTrue(known.exists() && orphan.exists() && active.exists())
    }

    @Test fun `an empty file that is being recorded is not deleted`() {
        val rec = tmp.newFolder("rec")
        val live = File(rec, "w-9.aac").apply { createNewFile() }

        val queued = RecordingCatalog.recoverOrphans(JsonQueueStore(qf), rec, { it == live }, now = 999)

        assertTrue("the live file must survive", live.exists())
        assertTrue(queued.isEmpty())
    }

    @Test fun `a recording that stops while recovery runs keeps the details the recorder gave it`() {
        val rec = tmp.newFolder("rec")
        val finished = File(rec, "w-9.aac").apply { writeBytes(ByteArray(64)) }
        val real = QueueItem("w-9", finished.path, 42, durationMs = 61_000, bytes = 64)
        val s = JsonQueueStore(qf)

        // The recorder queues the file, then stops counting it as active, after the directory was read.
        RecordingCatalog.recoverOrphans(s, rec, { s.put(real); false }, now = 999)

        assertEquals(real, s.get("w-9"))
    }
}
