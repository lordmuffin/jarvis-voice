package com.lordmuffin.jarvisvoice.speech

import org.junit.Assert.assertEquals
import org.junit.Test

class AndroidSilenceWindowTest {

    @Test fun `conversation ends at a short pause`() {
        assertEquals(2_500L, AndroidSpeechEngine.silenceWindowMs(ListenMode.CONVERSATION))
    }

    @Test fun `dictation waits out a minute of silence instead of ending at the first pause`() {
        assertEquals(60_000L, AndroidSpeechEngine.silenceWindowMs(ListenMode.DICTATION))
    }

    @Test fun `hold asks for a five minute window`() {
        assertEquals(5 * 60_000L, AndroidSpeechEngine.silenceWindowMs(ListenMode.HOLD))
    }
}
