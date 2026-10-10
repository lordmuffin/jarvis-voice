package com.lordmuffin.jarvisvoice.live

import com.lordmuffin.jarvisvoice.live.protocol.Channel
import com.lordmuffin.jarvisvoice.live.protocol.DraftSegment
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.concurrent.Executors
import kotlin.math.PI
import kotlin.math.sin

class VadSegmenterTest {

    private val frameSamples = 1600

    private fun tone() = ShortArray(frameSamples) { (3000 * sin(2 * PI * 440 * it / 16_000.0)).toInt().toShort() }
    private fun silence() = ShortArray(frameSamples)

    /** Feeds `spec` as (isSpeech, frames) runs from t=0; returns every event with its feed time. */
    private fun run(seg: VadSegmenter, vararg spec: Pair<Boolean, Int>): List<VadSegmenter.Event> {
        val events = ArrayList<VadSegmenter.Event>()
        var t = 0L
        for ((speech, count) in spec) repeat(count) {
            seg.feed(if (speech) tone() else silence(), t)?.let(events::add)
            t += 100
        }
        return events
    }

    @Test fun commitsAfterThreeSecondsOfSilenceWithPadding() {
        val events = run(VadSegmenter(), false to 10, true to 15, false to 30)
        val commit = events.filterIsInstance<VadSegmenter.Event.Commit>().single()
        // Speech 1000..2500 ms, with 300 ms of pre-roll and trailing pad.
        assertEquals(700L, commit.startMs)
        assertEquals(2800L, commit.endMs)
        assertEquals(((2800 - 700) * 16).toInt(), commit.samples.size)
    }

    @Test fun emitsPartialsEveryTwoSecondsDuringSpeech() {
        val events = run(VadSegmenter(), true to 50)
        val partials = events.filterIsInstance<VadSegmenter.Event.Partial>()
        assertEquals(2, partials.size)
        assertTrue(partials.all { it.startMs == 0L })
        assertEquals(listOf(2100L, 4100L), partials.map { it.endMs })
    }

    @Test fun forcesCommitAtTwentySeconds() {
        val commits = run(VadSegmenter(), true to 450).filterIsInstance<VadSegmenter.Event.Commit>()
        assertEquals(2, commits.size)
        assertEquals(0L, commits[0].startMs)
        assertEquals(20_000L, commits[0].endMs)
        assertEquals(20_000L, commits[1].startMs)
    }

    @Test fun ignoresBlipsShorterThanHalfASecond() {
        val events = run(VadSegmenter(), true to 1, false to 40)
        assertTrue(events.none { it is VadSegmenter.Event.Commit })
    }

    @Test fun silenceOnlyNeverCommits() {
        val seg = VadSegmenter()
        assertTrue(run(seg, false to 300).isEmpty())
        assertNull(seg.finish())
    }

    @Test fun finishFlushesBufferedSpeech() {
        val seg = VadSegmenter()
        run(seg, true to 12)
        val commit = seg.finish()!!
        assertEquals(0L, commit.startMs)
        assertEquals(1200L, commit.endMs)
        assertNull(seg.finish())
    }

    @Test fun transcriberEmitsDraftsAndSkipsNoiseTags() {
        val drafts = ArrayList<DraftSegment>()
        val texts = ArrayDeque(listOf("partial words", "[BLANK_AUDIO]", "hello there"))
        val transcriber = LiveTranscriber(
            recognize = { texts.removeFirstOrNull() ?: "" },
            onDraft = { synchronized(drafts) { drafts += it } },
            executor = Executors.newSingleThreadExecutor(),
        )
        var t = 0L
        fun feed(speech: Boolean, n: Int) = repeat(n) {
            transcriber.feed(if (speech) tone() else silence(), t); t += 100
        }
        feed(true, 25)      // one partial at 2 s
        Thread.sleep(50)    // let the partial finish so the next one isn't skipped as busy
        feed(true, 20)      // second partial at 4 s → "[BLANK_AUDIO]" is dropped
        Thread.sleep(50)
        feed(false, 30)     // commit → "hello there"
        transcriber.finish()

        assertEquals(listOf("partial words", "hello there"), drafts.map { it.text })
        assertFalse(drafts[0].final)
        assertTrue(drafts[1].final)
        assertEquals(Channel.MIC, drafts[1].channel)
        assertEquals(0L, drafts[1].startMs)
    }

    @Test fun recognizerFailureIsReportedNotThrownOnTheSttThread() {
        val errors = ArrayList<Throwable>()
        val uncaught = ArrayList<Throwable>()
        val drafts = ArrayList<DraftSegment>()
        var calls = 0
        val transcriber = LiveTranscriber(
            recognize = { if (calls++ == 0) throw IllegalStateException("native boom") else "still here" },
            onDraft = { synchronized(drafts) { drafts += it } },
            executor = Executors.newSingleThreadExecutor { r ->
                Thread(r).apply { setUncaughtExceptionHandler { _, t -> synchronized(uncaught) { uncaught += t } } }
            },
            onError = { synchronized(errors) { errors += it } },
        )
        var t = 0L
        fun feed(speech: Boolean, n: Int) = repeat(n) {
            transcriber.feed(if (speech) tone() else silence(), t); t += 100
        }
        feed(true, 25)      // partial at 2 s → recognizer throws
        Thread.sleep(50)
        feed(false, 30)     // commit → recognized normally
        transcriber.finish()

        assertEquals(listOf("native boom"), errors.map { it.message })
        assertTrue(uncaught.isEmpty())
        assertEquals(listOf("still here"), drafts.map { it.text })
    }
}
