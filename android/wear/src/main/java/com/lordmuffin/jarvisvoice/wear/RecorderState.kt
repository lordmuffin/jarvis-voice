package com.lordmuffin.jarvisvoice.wear

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.io.File

/** Whether a recording is running, shared by the screen, the tile and the complication. */
object RecorderState {
    data class Active(val id: String, val startedAtWallMs: Long, val startedAtElapsedMs: Long)

    private val _active = MutableStateFlow<Active?>(null)
    val active: StateFlow<Active?> = _active.asStateFlow()

    fun started(a: Active) { _active.value = a }
    fun stopped() { _active.value = null }

    /** True for the audio file of the recording running right now. */
    fun isActive(file: File): Boolean = _active.value?.id == file.nameWithoutExtension

    fun elapsedMs(now: Long, a: Active): Long = (now - a.startedAtElapsedMs).coerceAtLeast(0)

    /** `m:ss`, or `h:mm:ss` past an hour. */
    fun format(ms: Long): String {
        val s = ms / 1000
        return if (s >= 3600) "%d:%02d:%02d".format(s / 3600, s % 3600 / 60, s % 60) else "%d:%02d".format(s / 60, s % 60)
    }
}
