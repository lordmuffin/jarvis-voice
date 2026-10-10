package com.lordmuffin.jarvisvoice.live

import com.lordmuffin.jarvisvoice.live.protocol.Channel
import com.lordmuffin.jarvisvoice.live.protocol.DraftSegment
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

/**
 * On-device drafts for a Live session: [VadSegmenter] cuts the mic stream into utterances and an
 * offline recognizer (sherpa-onnx Whisper in the app, a fake in tests) transcribes them on one
 * background thread. Partials are `final=false` drafts; committed utterances are `final=true`.
 *
 * [feed] is called from the capture thread and never blocks on recognition: a partial is skipped
 * while the recognizer is still busy, so the queue cannot grow behind real time.
 */
class LiveTranscriber(
    private val recognize: (ShortArray) -> String,
    private val onDraft: (DraftSegment) -> Unit,
    private val channel: Channel = Channel.MIC,
    private val segmenter: VadSegmenter = VadSegmenter(),
    private val executor: ExecutorService = Executors.newSingleThreadExecutor { r -> Thread(r, "jarvis-live-stt") },
) {
    private val inFlight = AtomicInteger(0)

    fun feed(samples: ShortArray, tMs: Long) {
        when (val event = segmenter.feed(samples, tMs)) {
            is VadSegmenter.Event.Commit -> submit(event, final = true)
            is VadSegmenter.Event.Partial -> if (inFlight.get() == 0) submit(event, final = false)
            null -> Unit
        }
    }

    /**
     * Transcribes any buffered speech and waits (up to [timeoutMs]) for recognition to finish.
     * Returns false if recognition is still running.
     */
    fun finish(timeoutMs: Long = 30_000): Boolean {
        segmenter.finish()?.let { submit(it, final = true) }
        executor.shutdown()
        return executor.awaitTermination(timeoutMs, TimeUnit.MILLISECONDS)
    }

    /** Stops without waiting; pending recognition is dropped. */
    fun cancel() {
        executor.shutdownNow()
    }

    private fun submit(event: VadSegmenter.Event, final: Boolean) {
        inFlight.incrementAndGet()
        runCatching {
            executor.execute {
                try {
                    val text = recognize(event.samples).trim()
                    if (text.isNotEmpty() && !isNoise(text)) {
                        onDraft(DraftSegment(channel, event.startMs, event.endMs, text, final))
                    }
                } finally {
                    inFlight.decrementAndGet()
                }
            }
        }.onFailure { inFlight.decrementAndGet() }  // executor already shut down
    }

    private companion object {
        /** Whisper's non-speech tags, e.g. `[BLANK_AUDIO]` or `(music)`. */
        val NOISE = Regex("""^[\[(][^\])]*[\])]$""")

        fun isNoise(text: String) = NOISE.matches(text)
    }
}
