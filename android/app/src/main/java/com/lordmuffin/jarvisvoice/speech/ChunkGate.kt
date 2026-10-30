package com.lordmuffin.jarvisvoice.speech

/**
 * What [SherpaOnnxSpeechEngine]'s recording loop does with each mic chunk: keep buffering, transcribe the
 * buffer at a pause, drop a buffer that holds only silence, or end the session after the mode's silence
 * limit. Free of Android so it can be unit tested. Not thread-safe: the recording thread owns it.
 */
class ChunkGate(
    private val mode: ListenMode,
    private val flushSilenceMs: Int = 3_000,
    private val maxBufferMs: Int = 20_000,
) {
    sealed interface Step {
        data object Keep : Step
        /** Transcribe the buffer and clear it. */
        data object Flush : Step
        /** Clear the buffer without transcribing: it holds no speech, and Whisper invents words for silence. */
        data object Drop : Step
        /** The mode's silence limit is reached: end the session. */
        data object Stop : Step
    }

    private var pauseMs = 0      // silence since the last speech or flush
    private var quietMs = 0L     // silence since the last speech
    private var bufferedMs = 0

    /** Whether the audio buffered since the last flush has any speech in it. */
    var bufferHasSpeech = false
        private set

    fun onChunk(silent: Boolean, ms: Int): Step {
        bufferedMs += ms
        if (silent) {
            pauseMs += ms
            quietMs += ms
        } else {
            pauseMs = 0
            quietMs = 0
            bufferHasSpeech = true
        }
        val limit = mode.silenceStopMs
        if (limit != null && quietMs >= limit) return Step.Stop
        if (pauseMs < flushSilenceMs && bufferedMs < maxBufferMs) return Step.Keep
        val step = if (bufferHasSpeech) Step.Flush else Step.Drop
        pauseMs = 0
        bufferedMs = 0
        bufferHasSpeech = false
        return step
    }
}
