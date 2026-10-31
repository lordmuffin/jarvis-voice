package com.lordmuffin.jarvisvoice.live

/**
 * Spots the mic being taken away. When a call (or another higher-priority capture) wins the
 * microphone, Android keeps our `AudioRecord` running but feeds it exact zeros, with no error.
 * A real microphone never produces a long run of exact zeros, so [thresholdFrames] of them in a
 * row (1.5 s at 100 ms) means the audio is not ours any more.
 */
class SilenceDetector(private val thresholdFrames: Int = 15) {
    var silent: Boolean = false
        private set
    private var run = 0

    /** @return true if [silent] changed because of this frame. */
    fun onFrame(samples: ShortArray): Boolean {
        val zero = samples.all { it.toInt() == 0 }
        if (zero) {
            run++
            if (!silent && run >= thresholdFrames) {
                silent = true
                return true
            }
            return false
        }
        run = 0
        if (silent) {
            silent = false
            return true
        }
        return false
    }

    fun reset() {
        run = 0
        silent = false
    }
}
