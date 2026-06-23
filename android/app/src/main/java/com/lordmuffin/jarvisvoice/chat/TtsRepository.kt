package com.lordmuffin.jarvisvoice.chat

import android.content.Context
import android.os.Bundle
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.speech.tts.Voice
import com.lordmuffin.jarvisvoice.DebugLog
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.suspendCancellableCoroutine
import java.util.Locale
import kotlin.coroutines.resume

private const val UTTERANCE_ID = "jarvis_tts"

class TtsRepository(context: Context) {

    private val ready = CompletableDeferred<Boolean>()
    private lateinit var tts: TextToSpeech

    init {
        // Single init listener — do all setup here so the ready deferred is
        // guaranteed to complete. A second setOnInitListener call would replace
        // this one and leave ready suspended forever.
        tts = TextToSpeech(context) { status: Int ->
            if (status == TextToSpeech.SUCCESS) {
                tts.language = Locale.US
                tts.setSpeechRate(1.0f)
                tts.setPitch(1.0f)
                // Prefer a high-quality offline en-US neural voice when available
                val best = tts.voices
                    ?.filter { v ->
                        !v.isNetworkConnectionRequired &&
                        v.locale.language == "en" &&
                        v.quality >= Voice.QUALITY_HIGH
                    }
                    ?.maxByOrNull { it.quality }
                if (best != null) {
                    tts.voice = best
                    DebugLog.i("TTS", "Voice: ${best.name} quality=${best.quality}")
                }
                ready.complete(true)
            } else {
                DebugLog.e("TTS", "TextToSpeech init failed: $status")
                ready.complete(false)
            }
        }
    }

    // Speak text using Android on-device TTS. Suspends until the utterance
    // completes or is cancelled. No network required — playback is instant.
    suspend fun speak(text: String) {
        if (!ready.await()) {
            DebugLog.e("TTS", "Engine not ready — skipping speak()")
            return
        }

        suspendCancellableCoroutine { cont ->
            tts.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
                override fun onStart(utteranceId: String?) {}

                override fun onDone(utteranceId: String?) {
                    if (cont.isActive) cont.resume(Unit)
                }

                @Deprecated("Deprecated in API 21")
                override fun onError(utteranceId: String?) {
                    DebugLog.e("TTS", "Utterance error")
                    if (cont.isActive) cont.resume(Unit)
                }

                override fun onError(utteranceId: String?, errorCode: Int) {
                    DebugLog.e("TTS", "Utterance error $errorCode")
                    if (cont.isActive) cont.resume(Unit)
                }
            })

            val params = Bundle().apply {
                putFloat(TextToSpeech.Engine.KEY_PARAM_VOLUME, 1.0f)
            }
            val result = tts.speak(text, TextToSpeech.QUEUE_FLUSH, params, UTTERANCE_ID)
            if (result == TextToSpeech.ERROR) {
                DebugLog.e("TTS", "tts.speak() returned ERROR")
                if (cont.isActive) cont.resume(Unit)
                return@suspendCancellableCoroutine
            }

            cont.invokeOnCancellation { tts.stop() }
        }
    }

    fun stop() {
        if (::tts.isInitialized) tts.stop()
    }

    fun destroy() {
        if (::tts.isInitialized) {
            tts.stop()
            tts.shutdown()
        }
    }
}
