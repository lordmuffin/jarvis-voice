package com.lordmuffin.jarvisvoice.wear.sync

import com.google.android.gms.wearable.CapabilityInfo
import com.google.android.gms.wearable.DataEvent
import com.google.android.gms.wearable.DataEventBuffer
import com.google.android.gms.wearable.DataMapItem
import com.google.android.gms.wearable.WearableListenerService
import com.lordmuffin.jarvisvoice.wear.WearGraph

/** The phone changed something we care about: an ack frees a recording, a new node means it can be sent. */
class WearDataListenerService : WearableListenerService() {

    override fun onDataChanged(events: DataEventBuffer) {
        val graph = WearGraph.get(this)
        for (event in events) {
            if (event.type != DataEvent.TYPE_CHANGED) continue
            val path = event.dataItem.uri.path ?: continue
            DataLayerPull.apply(graph, path, DataMapItem.fromDataItem(event.dataItem))
        }
        SyncScheduler.kick(this)
    }

    override fun onCapabilityChanged(info: CapabilityInfo) {
        if (info.nodes.isNotEmpty()) SyncScheduler.kick(this)
    }
}
