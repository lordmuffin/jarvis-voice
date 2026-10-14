package com.lordmuffin.jarvisvoice.pill

import android.speech.SpeechRecognizer
import org.junit.Assert.assertEquals
import org.junit.Test

class PillFormatTest {

    @Test fun elapsedIsMinutesAndSeconds() {
        assertEquals("0:00", PillFormat.elapsed(0))
        assertEquals("0:04", PillFormat.elapsed(4_999))
        assertEquals("1:05", PillFormat.elapsed(65_000))
        assertEquals("18:42", PillFormat.elapsed(1_122_000))
    }

    @Test fun negativeElapsedClampsToZero() {
        assertEquals("0:00", PillFormat.elapsed(-300))
    }

    /** Recogniser error codes become the pill's error words instead of vanishing. */
    @Test fun engineErrorsMapToPillErrors() {
        assertEquals(PillError.NO_SPEECH, PillError.fromEngineError(SpeechRecognizer.ERROR_NO_MATCH))
        assertEquals(PillError.NO_SPEECH, PillError.fromEngineError(SpeechRecognizer.ERROR_SPEECH_TIMEOUT))
        assertEquals(PillError.MIC_OFF, PillError.fromEngineError(SpeechRecognizer.ERROR_AUDIO))
        assertEquals(PillError.MIC_OFF, PillError.fromEngineError(SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS))
        assertEquals(PillError.ENGINE_UNAVAILABLE, PillError.fromEngineError(SpeechRecognizer.ERROR_NETWORK))
        assertEquals(PillError.ENGINE_UNAVAILABLE, PillError.fromEngineError(SpeechRecognizer.ERROR_RECOGNIZER_BUSY))
    }

    /** `SherpaOnnxSpeechEngine` reports -1 when the model is not loaded. */
    @Test fun missingRecognizerIsEngineUnavailable() {
        assertEquals(PillError.ENGINE_UNAVAILABLE, PillError.fromEngineError(-1))
    }
}
