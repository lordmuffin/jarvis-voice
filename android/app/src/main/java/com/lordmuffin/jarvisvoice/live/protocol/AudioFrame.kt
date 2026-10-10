package com.lordmuffin.jarvisvoice.live.protocol

import java.nio.ByteBuffer
import java.nio.ByteOrder

/**
 * One binary audio frame (`protocol/v1/README.md`): a 12-byte little-endian header
 * `<BBHII` (version, channel, flags, seq, t_ms) followed by PCM16LE 16 kHz mono.
 */
class AudioFrame(
    val channel: Channel,
    val seq: Long,
    val tMs: Long,
    val payload: ByteArray,
) {
    init {
        require(seq in 0..U32_MAX) { "seq out of range: $seq" }
        require(tMs in 0..U32_MAX) { "t_ms out of range: $tMs" }
    }

    val durationMs: Int get() = payload.size / BYTES_PER_MS

    fun encode(): ByteArray {
        require(isValidPayloadSize(payload.size)) { "invalid payload size ${payload.size}" }
        val buf = ByteBuffer.allocate(HEADER_SIZE + payload.size).order(ByteOrder.LITTLE_ENDIAN)
        buf.put(VERSION.toByte())
        buf.put(channel.index.toByte())
        buf.putShort(0)
        buf.putInt(seq.toInt())
        buf.putInt(tMs.toInt())
        buf.put(payload)
        return buf.array()
    }

    override fun equals(other: Any?): Boolean =
        other is AudioFrame && channel == other.channel && seq == other.seq &&
            tMs == other.tMs && payload.contentEquals(other.payload)

    override fun hashCode(): Int = (channel.hashCode() * 31 + seq.hashCode()) * 31 + tMs.hashCode()

    companion object {
        const val VERSION = 1
        const val HEADER_SIZE = 12
        const val SAMPLE_RATE = 16_000
        const val BYTES_PER_MS = SAMPLE_RATE * 2 / 1000          // 32
        const val MIN_PAYLOAD = 20 * BYTES_PER_MS                 // 640
        const val MAX_PAYLOAD = 200 * BYTES_PER_MS                // 6400
        /** The phone always sends 100 ms frames, like the Mac. */
        const val FRAME_MS = 100
        const val FRAME_BYTES = FRAME_MS * BYTES_PER_MS           // 3200
        private const val U32_MAX = 0xFFFF_FFFFL

        fun isValidPayloadSize(size: Int): Boolean =
            size in MIN_PAYLOAD..MAX_PAYLOAD && size % 2 == 0

        fun decode(bytes: ByteArray): AudioFrame {
            require(bytes.size >= HEADER_SIZE) { "frame shorter than header" }
            val buf = ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN)
            val version = buf.get().toInt() and 0xFF
            require(version == VERSION) { "unsupported version $version" }
            val channel = Channel.fromIndex(buf.get().toInt() and 0xFF)
                ?: throw IllegalArgumentException("unknown channel")
            val flags = buf.getShort().toInt() and 0xFFFF
            require(flags == 0) { "unsupported flags $flags" }
            val seq = buf.getInt().toLong() and U32_MAX
            val tMs = buf.getInt().toLong() and U32_MAX
            val payload = bytes.copyOfRange(HEADER_SIZE, bytes.size)
            require(isValidPayloadSize(payload.size)) { "invalid payload size ${payload.size}" }
            return AudioFrame(channel, seq, tMs, payload)
        }
    }
}
