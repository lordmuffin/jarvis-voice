package com.lordmuffin.jarvisvoice.live

import kotlin.math.sqrt

/**
 * Splits a stream of 100 ms PCM frames into utterances for the offline Whisper recognizer, using
 * the same energy VAD thresholds as `SherpaOnnxSpeechEngine`: commit after 3 s of silence or 20 s
 * of audio, and offer a partial snapshot every 2 s while speech is ongoing. Times are audio time
 * (`t_ms`), so the output is deterministic.
 */
class VadSegmenter(
    private val silenceRms: Double = 200.0,
    private val commitSilenceMs: Long = 3_000,
    private val maxUtteranceMs: Long = 20_000,
    private val minUtteranceMs: Long = 500,
    private val partialIntervalMs: Long = 2_000,
    /** Silence kept before the first speech frame and after the last one. */
    private val padMs: Long = 300,
) {
    sealed class Event {
        abstract val samples: ShortArray
        abstract val startMs: Long
        abstract val endMs: Long

        class Partial(override val samples: ShortArray, override val startMs: Long, override val endMs: Long) : Event()
        class Commit(override val samples: ShortArray, override val startMs: Long, override val endMs: Long) : Event()
    }

    private class Frame(val samples: ShortArray, val tMs: Long, val speech: Boolean) {
        val endMs: Long get() = tMs + samples.size * 1000L / SAMPLE_RATE
    }

    private val frames = ArrayList<Frame>()
    private var hasSpeech = false
    private var silenceMs = 0L
    private var lastPartialMs = Long.MIN_VALUE

    /** Feeds one frame; returns at most one event. */
    fun feed(samples: ShortArray, tMs: Long): Event? {
        val frame = Frame(samples, tMs, rms(samples) >= silenceRms)
        val frameMs = frame.endMs - frame.tMs

        if (!hasSpeech && !frame.speech) {
            // Leading silence: keep only the pre-roll.
            frames.add(frame)
            while (frames.isNotEmpty() && frame.endMs - frames.first().tMs > padMs) frames.removeAt(0)
            return null
        }

        frames.add(frame)
        if (frame.speech) {
            if (!hasSpeech) lastPartialMs = frame.tMs
            hasSpeech = true
            silenceMs = 0
        } else {
            silenceMs += frameMs
        }

        val spanMs = frame.endMs - frames.first().tMs
        if (silenceMs >= commitSilenceMs || spanMs >= maxUtteranceMs) return commit()

        // Partials only while someone is talking: during a pause the text would not change.
        if (frame.speech && frame.tMs - lastPartialMs >= partialIntervalMs) {
            lastPartialMs = frame.tMs
            val (pcm, start, end) = trimmed()
            if (end - start >= minUtteranceMs) return Event.Partial(pcm, start, end)
        }
        return null
    }

    /** Flushes whatever speech is buffered (session stop). */
    fun finish(): Event.Commit? {
        if (!hasSpeech) {
            reset()
            return null
        }
        return commit()
    }

    private fun commit(): Event.Commit? {
        val (pcm, start, end) = trimmed()
        reset()
        if (end - start < minUtteranceMs) return null
        return Event.Commit(pcm, start, end)
    }

    /** The buffered audio with trailing silence trimmed to [padMs]. */
    private fun trimmed(): Triple<ShortArray, Long, Long> {
        val lastSpeech = frames.indexOfLast { it.speech }
        var lastIndex = frames.lastIndex
        if (lastSpeech >= 0) {
            val cutoff = frames[lastSpeech].endMs + padMs
            while (lastIndex > lastSpeech && frames[lastIndex].tMs >= cutoff) lastIndex--
        }
        val kept = frames.subList(0, lastIndex + 1)
        val pcm = ShortArray(kept.sumOf { it.samples.size })
        var offset = 0
        for (f in kept) {
            f.samples.copyInto(pcm, offset)
            offset += f.samples.size
        }
        return Triple(pcm, kept.first().tMs, kept.last().endMs)
    }

    private fun reset() {
        frames.clear()
        hasSpeech = false
        silenceMs = 0
    }

    companion object {
        const val SAMPLE_RATE = 16_000

        fun rms(samples: ShortArray): Double {
            if (samples.isEmpty()) return 0.0
            var sum = 0.0
            for (s in samples) sum += s.toDouble() * s.toDouble()
            return sqrt(sum / samples.size)
        }
    }
}
