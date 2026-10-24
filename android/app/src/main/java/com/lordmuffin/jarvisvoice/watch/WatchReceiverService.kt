package com.lordmuffin.jarvisvoice.watch

import com.google.android.gms.tasks.Tasks
import com.google.android.gms.wearable.ChannelClient
import com.google.android.gms.wearable.Wearable
import com.google.android.gms.wearable.WearableListenerService
import com.lordmuffin.jarvisvoice.DebugLog
import com.lordmuffin.jarvisvoice.wearcommon.AckStatus
import com.lordmuffin.jarvisvoice.wearcommon.WearPaths
import com.lordmuffin.jarvisvoice.wearcommon.inbox.ReceiveResult
import kotlinx.coroutines.runBlocking

/**
 * The phone end of the Data Layer channel. The watch opens a channel per recording; this reads the
 * stream into the [com.lordmuffin.jarvisvoice.wearcommon.inbox.WatchInbox], which keeps the file only
 * if every announced byte arrived, then hands it to [WatchUploadWorker].
 */
class WatchReceiverService : WearableListenerService() {

    override fun onChannelOpened(channel: ChannelClient.Channel) {
        if (!channel.path.startsWith(WearPaths.CHANNEL_RECORDING)) return
        val channels = Wearable.getChannelClient(this)
        val inbox = WatchUploadWorker.inbox(this)
        try {
            // Called on a background thread, so blocking on the Task is fine here.
            val input = Tasks.await(channels.getInputStream(channel))
            val result = input.use { inbox.receive(channel.path, it) }
            DebugLog.i(TAG, "watch recording: $result")
            when (result) {
                is ReceiveResult.Stored -> WatchUploadWorker.enqueue(this)
                is ReceiveResult.AlreadyHave -> {
                    // The watch is re-sending: it never saw our ack. Say it again, or make sure the upload runs.
                    if (result.done) runBlocking { WearBridge.publishAck(this@WatchReceiverService, result.meta.id, AckStatus.ACKED) }
                    else WatchUploadWorker.enqueue(this)
                }
                is ReceiveResult.Incomplete, ReceiveResult.BadPath -> Unit   // nothing kept, nothing acked: the watch retries
            }
        } catch (e: Exception) {
            DebugLog.e(TAG, "receiving from the watch failed: ${e.message}")
        } finally {
            channels.close(channel)
        }
    }

    private companion object {
        const val TAG = "WatchReceiver"
    }
}
