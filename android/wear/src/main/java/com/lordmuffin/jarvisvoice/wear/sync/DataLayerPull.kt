package com.lordmuffin.jarvisvoice.wear.sync

import android.content.Context
import android.net.Uri
import com.google.android.gms.wearable.DataClient
import com.google.android.gms.wearable.DataMapItem
import com.google.android.gms.wearable.PutDataRequest
import com.google.android.gms.wearable.Wearable
import com.lordmuffin.jarvisvoice.wear.WearGraph
import com.lordmuffin.jarvisvoice.wearcommon.AckStatus
import com.lordmuffin.jarvisvoice.wearcommon.WearPaths
import kotlinx.coroutines.tasks.await

/** Applies what the phone published: server acks and config. Idempotent, so it is safe to run on every sync. */
object DataLayerPull {

    suspend fun pull(ctx: Context, graph: WearGraph) {
        val uri = Uri.Builder().scheme(PutDataRequest.WEAR_URI_SCHEME).path("/jarvis/").build()
        val items = Wearable.getDataClient(ctx).getDataItems(uri, DataClient.FILTER_PREFIX).await()
        try {
            for (item in items) apply(graph, item.uri.path.orEmpty(), DataMapItem.fromDataItem(item))
        } finally {
            items.release()
        }
    }

    fun apply(graph: WearGraph, path: String, item: DataMapItem) {
        val map = item.dataMap
        when {
            path == WearPaths.CONFIG -> {
                val changed = graph.config.update(map.getString(WearPaths.CONFIG_KEY_URL), map.getString(WearPaths.CONFIG_KEY_TOKEN))
                if (changed) graph.runner.onConfigChanged()
            }
            WearPaths.ackIdFromPath(path) != null -> {
                val status = AckStatus.fromWire(map.getString(WearPaths.ACK_KEY_STATUS)) ?: return
                graph.runner.onAck(WearPaths.ackIdFromPath(path)!!, status)
            }
        }
    }
}
