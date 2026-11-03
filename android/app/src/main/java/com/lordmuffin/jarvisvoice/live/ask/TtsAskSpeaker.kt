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
    private val attributes = AudioAttributes.Builder()
        .setUsage(AudioAttributes.USAGE_MEDIA)
        .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
        .build()
    private val focus = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK)
        .setAudioAttributes(attributes)
        .setOnAudioFocusChangeListener { change ->
            if (change == AudioManager.AUDIOFOCUS_LOSS) stop()
        }
        .build()

    @Volatile private var ready = false
    private lateinit var tts: TextToSpeech
    @Volatile private var callbacks: Callbacks? = null

    private class Callbacks(
        val id: String,
        val onFirstAudio: () -> Unit,
        val onDone: () -> Unit,
        val onError: () -> Unit,
    ) {
        val heard = AtomicBoolean(false)
        val ended = AtomicBoolean(false)
    }

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
                callbacks?.takeIf { it.id == utteranceId }?.ended?.set(true)
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
        callbacks = Callbacks(id, onFirstAudio, onDone, onError)
        audio.requestAudioFocus(focus)
        if (tts.speak(text, TextToSpeech.QUEUE_FLUSH, Bundle(), id) == TextToSpeech.ERROR) end(id, ok = false)
    }

    override fun stop() {
        callbacks?.ended?.set(true)
        if (::tts.isInitialized) tts.stop()
        release()
    }

    fun destroy() {
        stop()
        if (::tts.isInitialized) tts.shutdown()
    }

    private fun first(id: String?) {
        val c = callbacks?.takeIf { it.id == id } ?: return
        if (!c.ended.get() && c.heard.compareAndSet(false, true)) c.onFirstAudio()
    }

    private fun end(id: String?, ok: Boolean) {
        val c = callbacks?.takeIf { it.id == id } ?: return
        release()
        if (!c.ended.compareAndSet(false, true)) return
        if (ok) c.onDone() else c.onError()
    }

    private fun release() {
        audio.abandonAudioFocusRequest(focus)
    }

    private companion object {
        const val TAG = "AskSpeaker"
    }
}
