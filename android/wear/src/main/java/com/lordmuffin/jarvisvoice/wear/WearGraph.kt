package com.lordmuffin.jarvisvoice.wear

import android.content.Context
import com.lordmuffin.jarvisvoice.wear.sync.PlayServicesTransport
import com.lordmuffin.jarvisvoice.wear.sync.WatchConfig
import com.lordmuffin.jarvisvoice.wear.sync.WearEnvironment
import com.lordmuffin.jarvisvoice.wearcommon.RecordingUploader
import com.lordmuffin.jarvisvoice.wearcommon.UploadResult
import com.lordmuffin.jarvisvoice.wearcommon.sync.DirectUploader
import com.lordmuffin.jarvisvoice.wearcommon.sync.JsonQueueStore
import com.lordmuffin.jarvisvoice.wearcommon.sync.SyncRunner
import com.lordmuffin.jarvisvoice.wearcommon.transfer.RecordingSender
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** The watch app's long-lived objects, built once per process. */
class WearGraph private constructor(app: Context) {
    val recordingsDir = File(app.filesDir, "recordings").also { it.mkdirs() }
    val store = JsonQueueStore(File(app.filesDir, "queue.json"))
    val config = WatchConfig(app)

    val runner = SyncRunner(
        store = store,
        phone = RecordingSender(PlayServicesTransport(app)),
        direct = DirectUploader { item, file ->
            val c = config.current() ?: return@DirectUploader UploadResult.Retry("server not configured")
            val title = "Watch memo " + SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.US).format(Date(item.startedAtMs))
            // Same id as the phone would use, so the server collapses a double upload into one session.
            RecordingUploader(c.serverUrl, c.token).upload(file, item.id, title, source = "watch")
        },
        environment = WearEnvironment(app, config),
    )

    companion object {
        @Volatile private var instance: WearGraph? = null
        fun get(ctx: Context): WearGraph =
            instance ?: synchronized(this) { instance ?: WearGraph(ctx.applicationContext).also { instance = it } }
    }
}
