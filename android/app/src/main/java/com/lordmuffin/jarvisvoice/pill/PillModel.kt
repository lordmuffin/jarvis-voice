package com.lordmuffin.jarvisvoice.pill

import android.speech.SpeechRecognizer
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow

/** What the dictation pill is showing (`Jarvis-And-Pill` in the Instrument design). */
sealed interface PillState {
    /** After this many ms the pill clears itself back to [Idle]; null = it waits for the next event. */
    val autoResetMs: Long? get() = null

    /** True while the mic or the transcriber is working, so hiding the overlay must stop it. */
    val busy: Boolean get() = false

    data object Idle : PillState
    data class Listening(val startedAtMs: Long, val held: Boolean) : PillState {
        override val busy get() = true
    }
    data object Processing : PillState {
        override val busy get() = true
    }
    /** [copied]: no field took the text, so it is only on the clipboard. */
    data class Done(val words: Int, val wpm: Int, val enhanced: Boolean, val copied: Boolean) : PillState {
        override val autoResetMs get() = DONE_MS
    }
    data class Error(val kind: PillError) : PillState {
        override val autoResetMs get() = ERROR_MS
    }

    companion object {
        const val DONE_MS = 1_500L
        const val ERROR_MS = 6_000L
    }
}

/** What a tap on the pill does. */
enum class PillAction { NONE, RETRY, OPEN_SETTINGS, OPEN_STT_SETTINGS }

/** Why dictation failed, in the words the pill shows. These used to be log lines or nothing. */
enum class PillError(val tapAction: PillAction) {
    /** Nothing usable was heard (silence, ambient noise, an empty transcript). */
    NO_SPEECH(PillAction.RETRY),
    /** The microphone permission is off or the recorder could not start. */
    MIC_OFF(PillAction.OPEN_SETTINGS),
    /** A Jarvis Live session has the microphone. */
    MIC_BUSY(PillAction.NONE),
    /** No speech model is downloaded or the engine failed to start. */
    ENGINE_UNAVAILABLE(PillAction.OPEN_STT_SETTINGS);

    companion object {
        /** From `SpeechEngine.onError`: a `SpeechRecognizer.ERROR_*` code, or -1 when no model is loaded. */
        fun fromEngineError(code: Int): PillError = when (code) {
            SpeechRecognizer.ERROR_NO_MATCH, SpeechRecognizer.ERROR_SPEECH_TIMEOUT -> NO_SPEECH
            SpeechRecognizer.ERROR_AUDIO, SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS -> MIC_OFF
            else -> ENGINE_UNAVAILABLE
        }
    }
}

object PillFormat {
    /** `m:ss`, as the pill's timer shows it. */
    fun elapsed(ms: Long): String {
        val total = (ms.coerceAtLeast(0) / 1000)
        return String.format(java.util.Locale.US, "%d:%02d", total / 60, total % 60)
    }
}

/**
 * The one place the dictation pill's state lives. The overlay service drives it, the pill view and
 * the Android Auto screen read it. Every call returns whether it changed anything; calls that make
 * no sense in the current state (a tap while processing, a late result after a failure) do nothing.
 */
class PillModel {
    private val _state = MutableStateFlow<PillState>(PillState.Idle)
    val state: StateFlow<PillState> = _state

    fun startListening(nowMs: Long, held: Boolean): Boolean = when (_state.value) {
        PillState.Idle, is PillState.Error, is PillState.Done -> set(PillState.Listening(nowMs, held))
        else -> false
    }

    fun stopListening(): Boolean =
        if (_state.value is PillState.Listening) set(PillState.Processing) else false

    /** A result can arrive while still listening: the engine ends tap-mode dictation itself after a minute of silence. */
    fun finish(done: PillState.Done): Boolean = when (_state.value) {
        PillState.Processing, is PillState.Listening -> set(done)
        else -> false
    }

    fun fail(kind: PillError): Boolean = set(PillState.Error(kind))

    fun reset(): Boolean = set(PillState.Idle)

    private fun set(next: PillState): Boolean {
        _state.value = next
        return true
    }
}

/** The pill the overlay service, the view and the car screen share. */
object DictationPill {
    val model = PillModel()
}
