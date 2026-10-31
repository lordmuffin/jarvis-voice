package com.lordmuffin.jarvisvoice.wear

import android.util.Log
import com.lordmuffin.jarvisvoice.wearcommon.sync.QueueItem
import com.lordmuffin.jarvisvoice.wearcommon.sync.QueueStore
import java.io.File

/** The microphone, reduced to what a recording needs, so tests can run one without hardware. */
interface AudioCapture {
    val isRecording: Boolean

    /** Audio on disk so far. */
    val bytesWritten: Long

    /** [onMaxDuration] and [onError] run on the main thread. */
    fun start(id: String, maxDurationMs: Int, onMaxDuration: () -> Unit, onError: () -> Unit): File
    fun stop(): File?
}

/**
 * One recording from start to queue, without the Android service around it. The order of events
 * here is what keeps audio from being lost to the sync pass running beside it:
 *  - a recording is published in [RecorderState] before its file exists, and
 *  - it is queued before it is unpublished,
 * so at every moment the file is either live or queued and orphan recovery never touches it.
 */
class RecordingSession(
    private val capture: AudioCapture,
    private val store: QueueStore,
    private val wallClock: () -> Long,
    private val elapsedClock: () -> Long,
    private val onChanged: (started: Boolean) -> Unit = {},
) {
    private var lastBytes = 0L
    private var quietChecks = 0

    val isRecording get() = capture.isRecording

    /** [onEnded] runs when the recording ends by itself, with the failure if there was one. Call [stop] from it. */
    fun start(onEnded: (RecorderState.Failure?) -> Unit): Boolean {
        val now = wallClock()
        val id = "w-$now"
        lastBytes = 0
        quietChecks = 0
        RecorderState.started(RecorderState.Active(id, now, elapsedClock()))
        try {
            capture.start(id, MAX_RECORDING_MS, { onEnded(null) }, { onEnded(RecorderState.Failure.STOPPED_EARLY) })
        } catch (e: Exception) {
            Log.e(TAG, "cannot start recording", e)
            RecorderState.stopped()
            RecorderState.failed(RecorderState.Failure.MIC_UNAVAILABLE)
            return false
        }
        notifyChanged(started = true)
        return true
    }

    /** Stops, keeps what is on disk and queues it. [failure] says why, if this is not the wearer's stop. */
    fun stop(failure: RecorderState.Failure? = null) {
        val active = RecorderState.active.value
        val file = capture.stop()
        var reported = failure
        if (active != null && file != null) {
            val durationMs = RecorderState.elapsedMs(elapsedClock(), active)
            val bytes = file.length()
            when {
                durationMs < MIN_RECORDING_MS -> file.delete()   // an accidental double tap: nothing worth keeping
                bytes == 0L -> {
                    file.delete()
                    reported = RecorderState.Failure.NOTHING_CAPTURED
                }
                else -> try {
                    store.put(QueueItem(active.id, file.absolutePath, active.startedAtWallMs, durationMs, bytes))
                } catch (e: Exception) {
                    // The audio is safe on disk: the next sync pass recovers it as an orphan.
                    Log.e(TAG, "cannot queue ${active.id}", e)
                }
            }
        }
        RecorderState.stopped()
        reported?.let(RecorderState::failed)
        notifyChanged(started = false)
    }

    /**
     * Call every [PROGRESS_CHECK_MS]. True once the file has stopped growing, the sign of a recorder
     * that died without saying so. Even silence grows the file, so this is never a quiet room.
     */
    fun stalled(): Boolean {
        val bytes = capture.bytesWritten
        quietChecks = if (bytes > lastBytes) 0 else quietChecks + 1
        lastBytes = bytes
        return quietChecks >= STALLED_AFTER_CHECKS
    }

    /** The tile, complication and haptics are decoration: a failure there must not touch the recording. */
    private fun notifyChanged(started: Boolean) {
        try {
            onChanged(started)
        } catch (e: Exception) {
            Log.w(TAG, "cannot refresh surfaces", e)
        }
    }

    companion object {
        private const val TAG = "WearRecording"
        const val PROGRESS_CHECK_MS = 10_000L
        private const val STALLED_AFTER_CHECKS = 2

        /** Safety cap: two hours is ~21 MB. Beyond that the recording ends and is queued like any other. */
        const val MAX_RECORDING_MS = 2 * 60 * 60 * 1000

        /** Shorter than this is a mis-tap. */
        const val MIN_RECORDING_MS = 700L
    }
}
