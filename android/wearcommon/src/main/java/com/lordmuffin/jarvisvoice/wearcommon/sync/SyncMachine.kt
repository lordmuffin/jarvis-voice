package com.lordmuffin.jarvisvoice.wearcommon.sync

import com.lordmuffin.jarvisvoice.wearcommon.UploadResult

enum class SyncState { RECORDED, AT_PHONE, ACKED, BLOCKED, QUARANTINED }

data class QueueItem(
    val id: String,
    val filePath: String,
    val startedAtMs: Long,
    val durationMs: Long,
    val bytes: Long,
    val state: SyncState = SyncState.RECORDED,
    val attempts: Int = 0,
    val nextAttemptAtMs: Long = 0,
    val atPhoneSinceMs: Long? = null,
    val lastError: String? = null,
)

data class SyncEnv(val phoneReachable: Boolean, val networkAvailable: Boolean, val serverConfigured: Boolean) {
    val canUploadDirect: Boolean get() = networkAvailable && serverConfigured
}

sealed interface SyncAction {
    data object SendToPhone : SyncAction
    data object UploadDirect : SyncAction
    data object DeleteLocal : SyncAction

    /** Nothing to do now; look again at [atMs] (null: when connectivity or config changes). */
    data class Wait(val atMs: Long?) : SyncAction
}

object SyncMachine {
    const val PHONE_ACK_TIMEOUT_MS = 15 * 60_000L
    const val BACKOFF_BASE_MS = 30_000L
    const val BACKOFF_MAX_MS = 30 * 60_000L

    /** 30 s, 60 s, 2 min ... capped at 30 min. */
    fun backoffMs(attempts: Int): Long {
        val doublings = (attempts - 1).coerceIn(0, 20)
        return minOf(BACKOFF_BASE_MS shl doublings, BACKOFF_MAX_MS)
    }

    /**
     * What to do with [item] now. Audio is only ever deleted from [SyncState.ACKED], which only
     * [uploadResult] (a 2xx from the server) or [serverAck] (the phone relaying one) can reach.
     */
    fun next(item: QueueItem, now: Long, env: SyncEnv): SyncAction = when (item.state) {
        SyncState.ACKED -> SyncAction.DeleteLocal
        SyncState.BLOCKED, SyncState.QUARANTINED -> SyncAction.Wait(null)
        SyncState.RECORDED -> route(item, now, env)
        SyncState.AT_PHONE -> {
            val deadline = (item.atPhoneSinceMs ?: now) + PHONE_ACK_TIMEOUT_MS
            if (now < deadline) SyncAction.Wait(deadline) else route(item, now, env, preferDirect = true)
        }
    }

    private fun route(item: QueueItem, now: Long, env: SyncEnv, preferDirect: Boolean = false): SyncAction = when {
        now < item.nextAttemptAtMs -> SyncAction.Wait(item.nextAttemptAtMs)
        preferDirect && env.canUploadDirect -> SyncAction.UploadDirect
        env.phoneReachable -> SyncAction.SendToPhone
        env.canUploadDirect -> SyncAction.UploadDirect
        else -> SyncAction.Wait(null)
    }

    /** The phone has the whole file. The watch keeps its copy until the server's ack comes back. */
    fun sentToPhone(item: QueueItem, now: Long): QueueItem = item.copy(
        state = SyncState.AT_PHONE,
        atPhoneSinceMs = now,
        attempts = 0,
        nextAttemptAtMs = 0,
        lastError = null,
    )

    fun phoneSendFailed(item: QueueItem, now: Long, reason: String): QueueItem = failed(item, now, reason)

    fun uploadResult(item: QueueItem, now: Long, result: UploadResult): QueueItem = when (result) {
        is UploadResult.Acked -> serverAck(item)
        is UploadResult.Retry -> failed(item.copy(state = SyncState.RECORDED, atPhoneSinceMs = null), now, result.reason)
        is UploadResult.AuthRejected -> item.copy(state = SyncState.BLOCKED, lastError = result.reason)
        is UploadResult.Rejected -> item.copy(state = SyncState.QUARANTINED, lastError = result.reason)
    }

    fun serverAck(item: QueueItem): QueueItem = item.copy(state = SyncState.ACKED, lastError = null)

    fun phoneRejected(item: QueueItem, reason: String): QueueItem =
        item.copy(state = SyncState.QUARANTINED, lastError = reason)

    /** New server URL or token: recordings blocked on the old one get a fresh start. */
    fun configChanged(item: QueueItem): QueueItem =
        if (item.state == SyncState.BLOCKED) {
            item.copy(state = SyncState.RECORDED, attempts = 0, nextAttemptAtMs = 0, lastError = null)
        } else {
            item
        }

    private fun failed(item: QueueItem, now: Long, reason: String): QueueItem {
        val attempts = item.attempts + 1
        return item.copy(attempts = attempts, nextAttemptAtMs = now + backoffMs(attempts), lastError = reason)
    }
}
