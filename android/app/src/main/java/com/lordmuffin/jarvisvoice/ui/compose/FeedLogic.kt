package com.lordmuffin.jarvisvoice.ui.compose

/**
 * Whether a live feed pins itself to the newest line. It follows by default, pauses when the user
 * drags away from the end, and resumes when they scroll back to the end or tap "Jump to live".
 * Lines arriving move the end away from the viewport without the user moving, so a scroll that
 * is not a drag never pauses it.
 */
class FollowLive {
    var following: Boolean = true
        private set

    fun onScroll(atEnd: Boolean, userDragging: Boolean) {
        if (atEnd) following = true else if (userDragging) following = false
    }

    fun jumpToLive() {
        following = true
    }
}

/** The last [capacity] input levels (0..1), oldest first, for the live waveform. */
class LevelHistory(private val capacity: Int) {
    private val samples = ArrayDeque<Float>(capacity)

    fun push(level: Float) {
        val v = if (level.isNaN()) 0f else level.coerceIn(0f, 1f)
        if (samples.size == capacity) samples.removeFirst()
        samples.addLast(v)
    }

    fun clear() = samples.clear()

    fun snapshot(): List<Float> = samples.toList()
}
