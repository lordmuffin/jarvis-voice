package com.lordmuffin.jarvisvoice.speech

import com.lordmuffin.jarvisvoice.speech.ChunkGate.Step
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ChunkGateTest {

    /** Feeds `spec` as (isSpeech, seconds) runs of 300 ms chunks; returns every step that wasn't Keep. */
    private fun run(gate: ChunkGate, vararg spec: Pair<Boolean, Int>): List<Step> {
        val steps = ArrayList<Step>()
        for ((speech, seconds) in spec) repeat(seconds * 1000 / 300) {
            val step = gate.onChunk(silent = !speech, ms = 300)
            if (step != Step.Keep) steps += step
            if (step == Step.Stop) return steps
        }
        return steps
    }

    @Test fun `a three second pause flushes the phrase`() {
        assertEquals(listOf(Step.Flush), run(ChunkGate(ListenMode.DICTATION), true to 5, false to 3))
    }

    @Test fun `twenty seconds of nonstop talk flushes without ending dictation`() {
        val steps = run(ChunkGate(ListenMode.DICTATION), true to 45)
        assertEquals(listOf(Step.Flush, Step.Flush), steps)
    }

    @Test fun `dictation keeps listening through pauses well past thirty seconds`() {
        val steps = run(
            ChunkGate(ListenMode.DICTATION),
            true to 15, false to 5, true to 15, false to 5, true to 15, false to 4,
        )
        assertFalse(Step.Stop in steps)
        assertEquals(3, steps.count { it == Step.Flush })
    }

    @Test fun `a pause with no speech in it is dropped, not transcribed`() {
        val steps = run(ChunkGate(ListenMode.DICTATION), true to 4, false to 10)
        assertEquals(Step.Flush, steps.first())
        assertTrue(steps.drop(1).all { it == Step.Drop })
    }

    @Test fun `dictation stops itself after a minute of silence`() {
        val steps = run(ChunkGate(ListenMode.DICTATION), true to 5, false to 65)
        assertEquals(Step.Stop, steps.last())
    }

    @Test fun `speech resets the silence clock`() {
        val steps = run(ChunkGate(ListenMode.DICTATION), false to 50, true to 2, false to 50)
        assertFalse(Step.Stop in steps)
    }

    @Test fun `hold and conversation never stop on silence alone`() {
        assertFalse(Step.Stop in run(ChunkGate(ListenMode.HOLD), true to 2, false to 120))
        assertFalse(Step.Stop in run(ChunkGate(ListenMode.CONVERSATION), false to 120))
    }

    @Test fun `only conversation ends at the first pause`() {
        assertTrue(ListenMode.CONVERSATION.endsAtPause)
        assertFalse(ListenMode.DICTATION.endsAtPause)
        assertFalse(ListenMode.HOLD.endsAtPause)
    }

    @Test fun `the buffer reports whether it holds speech`() {
        val gate = ChunkGate(ListenMode.HOLD)
        gate.onChunk(silent = true, ms = 300)
        assertFalse(gate.bufferHasSpeech)
        gate.onChunk(silent = false, ms = 300)
        assertTrue(gate.bufferHasSpeech)
        run(gate, false to 3)
        assertFalse(gate.bufferHasSpeech)
    }
}
