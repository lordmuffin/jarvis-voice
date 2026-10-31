package com.lordmuffin.jarvisvoice.wear.sync

import android.content.Context
import com.google.android.gms.wearable.CapabilityClient
import com.google.android.gms.wearable.Wearable
import com.lordmuffin.jarvisvoice.wearcommon.WearPaths
import com.lordmuffin.jarvisvoice.wearcommon.transfer.NodeTransport
import kotlinx.coroutines.tasks.await
import java.io.FilterOutputStream
import java.io.IOException
import java.io.OutputStream

/** [NodeTransport] over the real Wearable Data Layer: ChannelClient to a node with the phone capability. */
class PlayServicesTransport(private val context: Context) : NodeTransport {

    override suspend fun reachableNodeIds(): List<String> {
        val info = Wearable.getCapabilityClient(context)
            .getCapability(WearPaths.CAPABILITY_PHONE, CapabilityClient.FILTER_REACHABLE)
            .await()
        // Nearby (Bluetooth or same Wi-Fi) first; a cloud-bridged node still works, just slower.
        return info.nodes.sortedByDescending { it.isNearby }.map { it.id }
    }

    override suspend fun openOutput(nodeId: String, path: String): OutputStream {
        val channels = Wearable.getChannelClient(context)
        val channel = channels.openChannel(nodeId, path).await()
        val out = try {
            channels.getOutputStream(channel).await()
        } catch (e: Exception) {
            channels.close(channel)
            throw IOException("cannot open channel output", e)
        }
        return object : FilterOutputStream(out) {
            // Bulk writes straight through; FilterOutputStream would copy byte by byte.
            override fun write(b: ByteArray, off: Int, len: Int) = out.write(b, off, len)

            override fun close() {
                try {
                    super.close()
                } finally {
                    channels.close(channel)
                }
            }
        }
    }
}
