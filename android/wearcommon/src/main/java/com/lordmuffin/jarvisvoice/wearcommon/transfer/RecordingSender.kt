package com.lordmuffin.jarvisvoice.wearcommon.transfer

import com.lordmuffin.jarvisvoice.wearcommon.RecordingMeta
import com.lordmuffin.jarvisvoice.wearcommon.sync.PhoneLink
import com.lordmuffin.jarvisvoice.wearcommon.sync.QueueItem
import kotlinx.coroutines.CancellationException
import java.io.File
import java.io.IOException
import java.io.OutputStream

/**
 * The Data Layer, reduced to what the transfer needs, so the real thing (ChannelClient over Play
 * Services) and a fake node in tests are interchangeable.
 */
interface NodeTransport {
    /** Nearby nodes advertising the phone receiver capability. */
    suspend fun reachableNodeIds(): List<String>

    /** Opens a channel to [nodeId] at [path]; closing the stream closes the channel. */
    suspend fun openOutput(nodeId: String, path: String): OutputStream
}

/** The watch's half: streams one recording file to the phone over a channel. */
class RecordingSender(private val transport: NodeTransport) : PhoneLink {

    override suspend fun isReachable(): Boolean = transport.reachableNodeIds().isNotEmpty()

    override suspend fun send(item: QueueItem, file: File): Boolean {
        // The byte count in the path is the file's size right now, so the phone can tell a short copy.
        val meta = RecordingMeta(item.id, item.startedAtMs, item.durationMs, file.length())
        if (meta.bytes == 0L) return false
        for (node in transport.reachableNodeIds()) {
            try {
                transport.openOutput(node, meta.toPath()).use { out ->
                    file.inputStream().use { it.copyTo(out) }
                }
                return true
            } catch (e: CancellationException) {
                throw e
            } catch (e: IOException) {
                continue
            }
        }
        return false
    }
}
