package com.lordmuffin.jarvisvoice.live

import com.lordmuffin.jarvisvoice.live.protocol.AudioFrame
import com.lordmuffin.jarvisvoice.live.protocol.Channel
import com.lordmuffin.jarvisvoice.live.protocol.ChannelSeqs
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.io.RandomAccessFile

class OutboxException(message: String) : IOException(message)

/**
 * Durable, append-only store of audio frames for one session — a port of the Mac's
 * `JarvisLiveKit/Outbox`. Layout under `<root>/<sessionId>/`:
 *
 * - `<channel>.pcm`         — concatenated frame payloads
 * - `<channel>.index.jsonl` — one `{"seq","t_ms","offset","len"}` per line, appended after the
 *                             payload (a torn write leaves at worst an unindexed pcm tail, which is
 *                             truncated on reopen)
 * - `acked.json`            — `{"mic": n|null, "system": n|null}`, the cumulative server ack
 * - `meta.json`             — [SessionMeta]
 *
 * Only [markAcked] (driven by the server's `ack`) frees data. All methods are synchronized; they
 * are called from the capture thread and the connection's coroutines.
 */
class Outbox private constructor(val sessionId: String, val directory: File) {

    private data class IndexEntry(val seq: Long, val tMs: Long, val offset: Long, val len: Int)

    private class ChannelState(
        val entries: MutableList<IndexEntry>,
        val pcm: RandomAccessFile,
        val index: RandomAccessFile,
        var pcmEnd: Long,
    )

    private val channels = HashMap<Channel, ChannelState>()
    private var acked = ChannelSeqs()
    private var closed = false

    init {
        val ackedFile = File(directory, ACKED_FILE)
        if (ackedFile.exists()) {
            val o = JSONObject(ackedFile.readText())
            acked = ChannelSeqs(
                mic = if (o.isNull("mic")) null else o.getLong("mic"),
                system = if (o.isNull("system")) null else o.getLong("system"),
            )
        }
        for (channel in Channel.entries) channels[channel] = openChannel(channel, directory)
    }

    @Synchronized
    fun nextSeq(channel: Channel): Long {
        val fromEntries = channels[channel]?.entries?.lastOrNull()?.let { it.seq + 1 } ?: 0
        val fromAck = acked[channel]?.let { it + 1 } ?: 0
        return maxOf(fromEntries, fromAck)
    }

    @Synchronized fun ackedSeq(channel: Channel): Long? = acked[channel]

    @Synchronized fun ackedSeqs(): ChannelSeqs = acked

    /** Appends a frame. Its `seq` must equal [nextSeq]. */
    @Synchronized
    fun append(frame: AudioFrame) {
        check(!closed) { "outbox closed" }
        if (!AudioFrame.isValidPayloadSize(frame.payload.size)) {
            throw OutboxException("invalid payload size ${frame.payload.size}")
        }
        val expected = nextSeq(frame.channel)
        if (frame.seq != expected) {
            throw OutboxException("unexpected seq on ${frame.channel.wire}: expected $expected, got ${frame.seq}")
        }
        val state = channels.getValue(frame.channel)
        val entry = IndexEntry(frame.seq, frame.tMs, state.pcmEnd, frame.payload.size)
        state.pcm.seek(state.pcmEnd)
        state.pcm.write(frame.payload)
        // Index after payload: an index line never points at bytes that were not written.
        state.index.seek(state.index.length())
        state.index.write((entryJson(entry) + "\n").toByteArray())
        state.pcmEnd += frame.payload.size
        state.entries.add(entry)
    }

    /** Assigns the next seq and appends. Returns the stored frame. */
    @Synchronized
    fun append(channel: Channel, payload: ByteArray, tMs: Long): AudioFrame {
        val frame = AudioFrame(channel, nextSeq(channel), tMs, payload)
        append(frame)
        return frame
    }

    /** Unacked frames on [channel] with `seq >= from`, in seq order, at most [limit]. */
    @Synchronized
    fun unacked(channel: Channel, from: Long = 0, limit: Int = Int.MAX_VALUE): List<AudioFrame> {
        val state = channels[channel] ?: return emptyList()
        val floor = maxOf(from, acked[channel]?.let { it + 1 } ?: 0)
        val result = ArrayList<AudioFrame>()
        for (entry in state.entries) {
            if (entry.seq < floor) continue
            if (result.size >= limit) break
            val payload = ByteArray(entry.len)
            state.pcm.seek(entry.offset)
            state.pcm.readFully(payload)
            result.add(AudioFrame(channel, entry.seq, entry.tMs, payload))
        }
        return result
    }

    /** Audio duration (ms) of frames in `(acked, through]`; `through == null` means all stored. */
    @Synchronized
    fun unackedDurationMs(channel: Channel, through: Long? = null): Int {
        val state = channels[channel] ?: return 0
        val floor = acked[channel]?.let { it + 1 } ?: 0
        var bytes = 0L
        for (entry in state.entries) {
            if (entry.seq < floor) continue
            if (through != null && entry.seq > through) break
            bytes += entry.len
        }
        return (bytes / AudioFrame.BYTES_PER_MS).toInt()
    }

    @Synchronized
    fun totalUnackedMs(): Int = Channel.entries.sumOf { unackedDurationMs(it) }

    /** Records the server's cumulative ack. Lower or equal values are ignored. */
    @Synchronized
    fun markAcked(channel: Channel, seq: Long) {
        val current = acked[channel]
        if (current != null && seq <= current) return
        acked = acked.with(channel, seq)
        writeAtomically(File(directory, ACKED_FILE), acked.toJson().toString())
        compactIfFullyAcked(channel)
    }

    @Synchronized
    fun close() {
        if (closed) return
        closed = true
        for (state in channels.values) {
            runCatching { state.pcm.close() }
            runCatching { state.index.close() }
        }
        channels.clear()
    }

    private fun compactIfFullyAcked(channel: Channel) {
        val state = channels[channel] ?: return
        val last = state.entries.lastOrNull()?.seq ?: return
        val ack = acked[channel] ?: return
        if (ack < last || state.pcmEnd < COMPACTION_THRESHOLD_BYTES) return
        state.pcm.setLength(0)
        state.index.setLength(0)
        state.entries.clear()
        state.pcmEnd = 0
    }

    companion object {
        /** Once a channel is fully acked and its pcm file exceeds this size, the files are truncated. */
        const val COMPACTION_THRESHOLD_BYTES = 4L * 1024 * 1024
        private const val ACKED_FILE = "acked.json"

        /** Creates the session directory if needed, or opens and recovers an existing one. */
        fun open(root: File, sessionId: String): Outbox {
            val dir = File(root, sessionId)
            if (!dir.isDirectory && !dir.mkdirs()) throw OutboxException("cannot create $dir")
            return Outbox(sessionId, dir)
        }

        /** Opens an existing session after a crash or relaunch. */
        fun reopen(root: File, sessionId: String): Outbox {
            val dir = File(root, sessionId)
            if (!dir.isDirectory) throw OutboxException("session not found: $sessionId")
            return Outbox(sessionId, dir)
        }

        /** Renames a closed session directory (local-only id → server id before upload). */
        fun rename(root: File, from: String, to: String) {
            val src = File(root, from)
            val dst = File(root, to)
            if (!src.renameTo(dst)) throw OutboxException("cannot rename $from to $to")
        }

        private fun entryJson(e: IndexEntry): String =
            JSONObject().put("seq", e.seq).put("t_ms", e.tMs).put("offset", e.offset).put("len", e.len).toString()

        private fun openChannel(channel: Channel, dir: File): ChannelState {
            val pcmFile = File(dir, "${channel.wire}.pcm")
            val indexFile = File(dir, "${channel.wire}.index.jsonl")
            val pcmSize = if (pcmFile.exists()) pcmFile.length() else 0L

            // Recover: keep the longest valid, contiguous prefix of the index whose data exists.
            val raw = if (indexFile.exists()) indexFile.readBytes() else ByteArray(0)
            val entries = ArrayList<IndexEntry>()
            var validIndexBytes = 0
            var dataEnd = 0L
            var cursor = 0
            while (true) {
                val newline = raw.indexOf('\n'.code.toByte(), cursor)
                if (newline < 0) break
                val entry = runCatching {
                    val o = JSONObject(String(raw, cursor, newline - cursor))
                    IndexEntry(o.getLong("seq"), o.getLong("t_ms"), o.getLong("offset"), o.getInt("len"))
                }.getOrNull() ?: break
                if (entry.offset != dataEnd || entry.offset + entry.len > pcmSize) break
                val prev = entries.lastOrNull()
                if (prev != null && prev.seq + 1 != entry.seq) break
                entries.add(entry)
                dataEnd = entry.offset + entry.len
                cursor = newline + 1
                validIndexBytes = cursor
            }

            val pcm = RandomAccessFile(pcmFile, "rw")
            val index = RandomAccessFile(indexFile, "rw")
            pcm.setLength(dataEnd)
            index.setLength(validIndexBytes.toLong())
            return ChannelState(entries, pcm, index, dataEnd)
        }

        private fun ByteArray.indexOf(b: Byte, from: Int): Int {
            for (i in from until size) if (this[i] == b) return i
            return -1
        }

        internal fun writeAtomically(file: File, text: String) {
            val tmp = File(file.parentFile, file.name + ".tmp")
            tmp.writeText(text)
            if (!tmp.renameTo(file)) {
                file.delete()
                if (!tmp.renameTo(file)) throw OutboxException("cannot write ${file.name}")
            }
        }
    }
}
