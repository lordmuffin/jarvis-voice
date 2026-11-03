package com.lordmuffin.jarvisvoice.live.ask

import android.media.AudioDeviceInfo
import com.lordmuffin.jarvisvoice.live.protocol.ClientMessage
import com.lordmuffin.jarvisvoice.live.protocol.ServerEvent
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.util.UUID

/** What the ask control shows. */
sealed interface AskUi {
    /** The server has not offered asks: local-only, voice off, an old server, or no connection. */
    data object Hidden : AskUi
    data object Ready : AskUi
    data object Listening : AskUi
    data object Waiting : AskUi
    data class Speaking(val text: String) : AskUi
    data class Answered(val text: String, val spoken: Boolean, val why: Why? = null) : AskUi

    /** Why an answer was shown and not spoken. */
    enum class Why { ROUTE, STALE, NOTHING_HEARD, SPEECH_FAILED, INTERRUPTED }
}

/** Speech on this device. Callbacks may come on any thread. */
interface AskSpeaker {
    fun speak(text: String, onFirstAudio: () -> Unit, onDone: () -> Unit, onError: () -> Unit)
    fun stop()
}

/** Which outputs keep Jarvis in your ears (docs/plans/voice-push-to-talk.md, Headphones only). */
object OutputRoutes {
    private val CLOSED = setOf(
        AudioDeviceInfo.TYPE_WIRED_HEADSET,
        AudioDeviceInfo.TYPE_WIRED_HEADPHONES,
        AudioDeviceInfo.TYPE_USB_HEADSET,
        AudioDeviceInfo.TYPE_BLUETOOTH_A2DP,
        AudioDeviceInfo.TYPE_BLE_HEADSET,
        AudioDeviceInfo.TYPE_HEARING_AID,
    )

    fun isClosed(type: Int): Boolean = type in CLOSED

    /** Every output the speech would go to is closed, and there is at least one. */
    fun allClosed(types: List<Int>): Boolean = types.isNotEmpty() && types.all(::isClosed)
}

/**
 * Hold to ask, on the phone: markers out on the audio frames' clock, the reply spoken only into
 * closed headphones, and what happened reported back. No Android here, so all of it is unit-tested;
 * the service supplies the speaker, the route check and the send.
 */
class AskController(
    private val send: (ClientMessage) -> Unit,
    private val speaker: AskSpeaker,
    private val routeClosed: () -> Boolean,
    private val clock: () -> Long,
    /** Now, on the audio frames' clock. */
    private val streamMs: () -> Long,
    private val newId: () -> String = { UUID.randomUUID().toString().take(8) },
    private val staleMs: Long = STALE_MS,
) {
    private val _ui = MutableStateFlow<AskUi>(AskUi.Hidden)
    val ui: StateFlow<AskUi> = _ui.asStateFlow()

    private val lock = Any()
    private var available = false
    private var current: String? = null
    private var releasedAt = 0L
    private var playing: Pair<String, String>? = null  // id, text: handed to the speaker

    fun onAvailable(value: Boolean) = synchronized(lock) {
        available = value
        if (!value) {
            stopPlaying(reason = null)
            current = null
            _ui.value = AskUi.Hidden
        } else if (_ui.value == AskUi.Hidden) {
            _ui.value = AskUi.Ready
        }
    }

    fun down(tMs: Long) = synchronized(lock) {
        if (!available) return
        stopPlaying(reason = "superseded")
        val id = newId()
        current = id
        send(ClientMessage.AskBegin(id, tMs))
        _ui.value = AskUi.Listening
    }

    fun up(tMs: Long) = synchronized(lock) {
        val id = current ?: return
        if (_ui.value != AskUi.Listening) return
        send(ClientMessage.AskEnd(id, tMs))
        releasedAt = clock()
        _ui.value = AskUi.Waiting
    }

    fun onReply(reply: ServerEvent.AskReply) = synchronized(lock) {
        if (reply.id != current || _ui.value != AskUi.Waiting) return
        val text = reply.text
        when {
            text.isBlank() -> answered("", AskUi.Why.NOTHING_HEARD)
            clock() - releasedAt > staleMs -> skip(reply.id, text, "stale", AskUi.Why.STALE)
            !routeClosed() -> skip(reply.id, text, "route", AskUi.Why.ROUTE)
            else -> {
                playing = reply.id to text
                speaker.speak(
                    text,
                    onFirstAudio = { firstAudio(reply.id, text) },
                    onDone = { finished(reply.id, text) },
                    onError = { failed(reply.id, text) },
                )
            }
        }
    }

    /** The output route changed. Leaving closed headphones stops speech at once. */
    fun onRouteChanged(closed: Boolean) = synchronized(lock) {
        if (closed) return
        val (_, text) = playing ?: return
        stopPlaying(reason = "interrupted")
        _ui.value = AskUi.Answered(text, spoken = false, why = AskUi.Why.INTERRUPTED)
    }

    private fun firstAudio(id: String, text: String) = synchronized(lock) {
        if (playing?.first != id) return
        send(ClientMessage.AskPlayed(id, "started", streamMs(), latencyMs = clock() - releasedAt))
        _ui.value = AskUi.Speaking(text)
    }

    private fun finished(id: String, text: String) = synchronized(lock) {
        if (playing?.first != id) return
        playing = null
        send(ClientMessage.AskPlayed(id, "done", streamMs()))
        _ui.value = AskUi.Answered(text, spoken = true)
    }

    private fun failed(id: String, text: String) = synchronized(lock) {
        if (playing?.first != id) return
        playing = null
        send(ClientMessage.AskPlayed(id, "skipped", streamMs(), reason = "tts_failed"))
        answered(text, AskUi.Why.SPEECH_FAILED)
    }

    private fun skip(id: String, text: String, reason: String, why: AskUi.Why) {
        send(ClientMessage.AskPlayed(id, "skipped", streamMs(), reason = reason))
        answered(text, why)
    }

    private fun answered(text: String, why: AskUi.Why) {
        _ui.value = AskUi.Answered(text, spoken = false, why = why)
    }

    /** Stops speech in progress. [reason] null: the connection is gone, so nothing is reported. */
    private fun stopPlaying(reason: String?) {
        val (id, _) = playing ?: return
        playing = null
        speaker.stop()
        if (reason != null) send(ClientMessage.AskPlayed(id, "skipped", streamMs(), reason = reason))
    }

    companion object {
        /** A reply later than this after release is shown, not spoken: its moment has passed. */
        const val STALE_MS = 30_000L
    }
}
