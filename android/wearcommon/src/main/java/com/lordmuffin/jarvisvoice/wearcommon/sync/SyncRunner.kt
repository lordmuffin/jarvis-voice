package com.lordmuffin.jarvisvoice.wearcommon.sync

import com.lordmuffin.jarvisvoice.wearcommon.AckStatus
import com.lordmuffin.jarvisvoice.wearcommon.UploadResult
import kotlinx.coroutines.CancellationException
import java.io.File

/** The path to the paired phone. */
interface PhoneLink {
    suspend fun isReachable(): Boolean

    /** True only if the whole file was handed to the phone. */
    suspend fun send(item: QueueItem, file: File): Boolean
}

/** The path straight to the server over the watch's own Wi-Fi/LTE. */
fun interface DirectUploader {
    suspend fun upload(item: QueueItem, file: File): UploadResult
}

interface SyncEnvironment {
    fun networkAvailable(): Boolean
    fun serverConfigured(): Boolean
}

data class RunReport(
    /** When the soonest backoff or ack-wait ends; null if nothing is time-bound. */
    val wakeAtMs: Long?,
    /** Recordings are waiting for a phone or a network; run again when either appears. */
    val waitingForConnectivity: Boolean,
    /** Recordings still on the watch, whatever their state. */
    val remaining: Int,
)

/**
 * Drives the queue through [SyncMachine]: for each recording picks the phone or the direct route,
 * records what happened, and deletes audio only after the server's ack. One pass per call, so it
 * runs the same from WorkManager, a connectivity callback or an incoming ack.
 */
class SyncRunner(
    private val store: QueueStore,
    private val phone: PhoneLink,
    private val direct: DirectUploader,
    private val environment: SyncEnvironment,
    private val clock: () -> Long = System::currentTimeMillis,
) {
    suspend fun runOnce(): RunReport {
        val now = clock()
        val env = SyncEnv(
            phoneReachable = safe(false) { phone.isReachable() },
            networkAvailable = environment.networkAvailable(),
            serverConfigured = environment.serverConfigured(),
        )
        var connectivity = false
        for (queued in store.all().sortedBy { it.startedAtMs }) {
            var item = queued
            val file = File(item.filePath)
            if (item.state != SyncState.ACKED && item.state != SyncState.QUARANTINED && !file.exists()) {
                // Never silently drop an entry: mark it so it is visible, and keep the row.
                store.put(item.copy(state = SyncState.QUARANTINED, lastError = "audio file missing"))
                continue
            }
            when (val action = SyncMachine.next(item, now, env)) {
                SyncAction.DeleteLocal -> settle(item)
                SyncAction.SendToPhone -> {
                    if (safe(false) { phone.send(item, file) }) {
                        store.put(SyncMachine.sentToPhone(item, now))
                    } else {
                        item = SyncMachine.phoneSendFailed(item, now, "could not send to phone").also(store::put)
                        // The phone failed us; don't leave the recording idle if the watch can upload.
                        if (env.canUploadDirect) uploadDirect(item, file, now) else connectivity = true
                    }
                }
                SyncAction.UploadDirect -> uploadDirect(item, file, now)
                is SyncAction.Wait ->
                    if (action.atMs == null && (item.state == SyncState.RECORDED || item.state == SyncState.AT_PHONE)) {
                        connectivity = true
                    }
            }
        }
        val left = store.all()
        return RunReport(wakeAt(left, now), connectivity, left.size)
    }

    /** The soonest moment a backoff or an ack-wait ends, over the queue as it stands after the pass. */
    private fun wakeAt(items: List<QueueItem>, now: Long): Long? = items.mapNotNull {
        when (it.state) {
            SyncState.RECORDED -> it.nextAttemptAtMs.takeIf { t -> t > now }
            SyncState.AT_PHONE ->
                maxOf((it.atPhoneSinceMs ?: now) + SyncMachine.PHONE_ACK_TIMEOUT_MS, it.nextAttemptAtMs)
            else -> null
        }
    }.minOrNull()

    /** The phone relayed the server's verdict (a DataItem). Settles the recording and frees its audio. */
    fun onAck(id: String, status: AckStatus) {
        val item = store.get(id) ?: return
        if (status == AckStatus.ACKED) {
            settle(SyncMachine.serverAck(item))
        } else {
            store.put(SyncMachine.phoneRejected(item, "rejected by server"))
        }
    }

    /** The phone published a new server URL or token. */
    fun onConfigChanged() {
        store.all().forEach { store.put(SyncMachine.configChanged(it)) }
    }

    private suspend fun uploadDirect(item: QueueItem, file: File, now: Long) {
        val result = try {
            direct.upload(item, file)
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            UploadResult.Retry(e.message ?: "upload failed")
        }
        val next = SyncMachine.uploadResult(item, now, result)
        if (next.state == SyncState.ACKED) settle(next) else store.put(next)
    }

    /** Frees the audio of an acked recording. The row stays if the file cannot be deleted. */
    private fun settle(acked: QueueItem) {
        store.put(acked)
        val file = File(acked.filePath)
        if (!file.exists() || file.delete()) store.remove(acked.id)
    }

    private inline fun <T> safe(default: T, block: () -> T): T = try {
        block()
    } catch (e: CancellationException) {
        throw e
    } catch (e: Exception) {
        default
    }
}
