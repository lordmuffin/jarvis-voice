package com.lordmuffin.jarvisvoice.watch

import android.content.Context
import android.net.Uri
import android.util.Log
import com.google.android.gms.wearable.PutDataMapRequest
import com.google.android.gms.wearable.PutDataRequest
import com.google.android.gms.wearable.Wearable
import com.lordmuffin.jarvisvoice.live.LiveSettings
import com.lordmuffin.jarvisvoice.wearcommon.AckStatus
import com.lordmuffin.jarvisvoice.wearcommon.WearPaths
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import kotlinx.coroutines.tasks.await

/**
 * Everything the phone says to the watch: the server and token it may upload with, and, per
 * recording, what the server answered. Data items persist and sync to the watch whenever it is
 * connected, so nothing here needs the watch to be reachable right now.
 */
object WearBridge {
    private const val TAG = "WearBridge"
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    /** Fire and forget, for settings changes; Play Services being absent or the watch away is not an error. */
    fun publishConfigAsync(ctx: Context) {
        val app = ctx.applicationContext
        scope.launch { runCatching { publishConfig(app) }.onFailure { Log.i(TAG, "config not published: ${it.message}") } }
    }

    suspend fun publishConfig(ctx: Context) {
        val settings = LiveSettings(ctx)
        val client = Wearable.getDataClient(ctx)
        val token = settings.deviceToken
        if (token.isNullOrBlank()) {
            client.deleteDataItems(uri(WearPaths.CONFIG)).await()   // signed out: the watch must stop uploading
            return
        }
        val req = PutDataMapRequest.create(WearPaths.CONFIG).apply {
            dataMap.putString(WearPaths.CONFIG_KEY_URL, settings.serverUrl)
            dataMap.putString(WearPaths.CONFIG_KEY_TOKEN, token)
        }.asPutDataRequest().setUrgent()
        client.putDataItem(req).await()
    }

    /** Tells the watch what the server said about [recordingId]. Throws if it could not be handed to Play Services, so the caller retries. */
    suspend fun publishAck(ctx: Context, recordingId: String, status: AckStatus) {
        val req = PutDataMapRequest.create(WearPaths.ackPath(recordingId)).apply {
            dataMap.putString(WearPaths.ACK_KEY_STATUS, status.wire)
            // Same status twice is a no-op to Play Services; the timestamp makes a re-ack reach the watch again.
            dataMap.putLong("at", System.currentTimeMillis())
        }.asPutDataRequest().setUrgent()
        Wearable.getDataClient(ctx).putDataItem(req).await()
    }

    private fun uri(path: String): Uri = Uri.Builder().scheme(PutDataRequest.WEAR_URI_SCHEME).path(path).build()
}
