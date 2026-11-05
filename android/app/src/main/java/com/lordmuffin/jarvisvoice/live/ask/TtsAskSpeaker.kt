package com.lordmuffin.jarvisvoice.live.ask

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioFocusRequest
import android.media.AudioManager
import android.os.Bundle
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import com.lordmuffin.jarvisvoice.DebugLog
import java.util.Locale
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Speaks an ask's reply with the phone's own `TextToSpeech`. Built at session start, because a cold
 * engine takes seconds. **Offline voices only:** a network voice would send the reply's text to a
 * third party, so without an offline English voice nothing is spoken and the answer stays text.
 *
 * Media usage and speech content, as the chat path chose after `USAGE_ASSISTANT` proved unreliable
 * over A2DP. Other audio ducks while Jarvis speaks, and recovers after.
 */
class TtsAskSpeaker(context: Context) : AskSpeaker {
    private val audio = context.getSystemService(AudioManager::class.java)
    private val attributes = ATTRIBUTES
    private val focus = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK)
        .setAudioAttributes(attributes)
        .setOnAudioFocusChangeListener { change ->
            if (change == AudioManager.AUDIOFOCUS_LOSS) loseFocus()
        }
        .build()

    @Volatile private var ready = false
    private lateinit var tts: TextToSpeech
    @Volatile private var current: Utterance? = null

    init {
        tts = TextToSpeech(context) { status ->
            ready = status == TextToSpeech.SUCCESS && chooseOfflineVoice()
            if (!ready) DebugLog.e(TAG, "no offline TTS voice (status $status): asks will be text only")
        }
        tts.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
            override fun onStart(utteranceId: String?) = Unit

            // The first chunk of synthesized audio is the nearest callback to sound. Engines that
            // never call it report the first word instead (onRangeStart).
            override fun onAudioAvailable(utteranceId: String?, audio: ByteArray?) = first(utteranceId)

            override fun onRangeStart(utteranceId: String?, start: Int, end: Int, frame: Int) = first(utteranceId)

            override fun onDone(utteranceId: String?) = end(utteranceId, ok = true)

            @Deprecated("Deprecated in API 21")
            override fun onError(utteranceId: String?) = end(utteranceId, ok = false)

            override fun onError(utteranceId: String?, errorCode: Int) = end(utteranceId, ok = false)

            override fun onStop(utteranceId: String?, interrupted: Boolean) {
                // Our own stop() already reported; a stop from elsewhere is the end of what played.
                current?.takeIf { it.id == utteranceId }?.lostFocus()
                release()
            }
        })
    }

    private fun chooseOfflineVoice(): Boolean {
        tts.language = Locale.US
        tts.setAudioAttributes(attributes)
        val offline = tts.voices
            ?.filter { !it.isNetworkConnectionRequired && it.locale.language == "en" }
            ?.maxByOrNull { it.quality }
            ?: return false
        tts.voice = offline
        DebugLog.i(TAG, "voice ${offline.name}, quality ${offline.quality}")
        return true
    }

    override fun speak(text: String, onFirstAudio: () -> Unit, onDone: () -> Unit, onError: () -> Unit) {
        if (!ready || tts.voice?.isNetworkConnectionRequired != false) return onError()
        val id = "ask-${System.nanoTime()}"
        current = Utterance(id, onFirstAudio, onDone, onError)
        audio.requestAudioFocus(focus)
        if (tts.speak(text, TextToSpeech.QUEUE_FLUSH, Bundle(), id) == TextToSpeech.ERROR) end(id, ok = false)
    }

    override fun stop() {
        current?.cancel()
        if (::tts.isInitialized) tts.stop()
        release()
    }

    /** Another app took the audio: stop, and tell the caller how far the reply got. */
    private fun loseFocus() {
        val u = current
        if (::tts.isInitialized) tts.stop()
        release()
        u?.lostFocus()
    }

    fun destroy() {
        stop()
        if (::tts.isInitialized) tts.shutdown()
    }

    private fun first(id: String?) {
        current?.takeIf { it.id == id }?.audio()
    }

    private fun end(id: String?, ok: Boolean) {
        val u = current?.takeIf { it.id == id } ?: return
        release()
        u.finish(ok)
    }

    private fun release() {
        audio.abandonAudioFocusRequest(focus)
    }

    companion object {
        private const val TAG = "AskSpeaker"

        /** A spoken reply: media usage and speech content, on the speaker as in headphones. */
        val ATTRIBUTES: AudioAttributes = AudioAttributes.Builder()
            .setUsage(AudioAttributes.USAGE_MEDIA)
            .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
            .build()
    }
}

/**
 * One reply's reports to the caller: [onFirstAudio] once, then exactly one of [onDone] or [onError],
 * and nothing after the caller's own [cancel]. Losing audio focus ends what played as done, or, if
 * nothing had, as an error: either way the caller always hears how it ended.
 */
internal class Utterance(
    val id: String,
    private val onFirstAudio: () -> Unit,
    private val onDone: () -> Unit,
    private val onError: () -> Unit,
) {
    private val heard = AtomicBoolean(false)
    private val ended = AtomicBoolean(false)

    fun audio() {
        if (!ended.get() && heard.compareAndSet(false, true)) onFirstAudio()
    }

    fun finish(ok: Boolean) {
        if (!ended.compareAndSet(false, true)) return
        if (ok) onDone() else onError()
    }

    fun lostFocus() = finish(ok = heard.get())

    fun cancel() = ended.set(true)
}
