package com.lordmuffin.jarvisvoice.chat

import android.content.Context
import android.os.Bundle
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import com.lordmuffin.jarvisvoice.DebugLog
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.suspendCancellableCoroutine
import java.util.Locale
import kotlin.coroutines.resume

private const val UTTERANCE_ID = "jarvis_tts"

class TtsRepository(context: Context) {

    private val ready = CompletableDeferred<Boolean>()

    private val tts = TextToSpeech(context) { status ->
        if (status == TextToSpeech.SUCCESS) {
            ready.complete(true)
        } else {
            DebugLog.e("TTS", "TextToSpeech init failed: $status")
            ready.complete(false)
        }
    }

    init {
        // After init, select the best available en-US voice and set a natural pace
        tts.setOnInitListener { status ->
            if (status == TextToSpeech.SUCCESS) {
                tts.language = Locale.US
                // Prefer a high-quality neural voice if the device has one
                val best = tts.voices
                    ?.filter { v ->
                        !v.isNetworkConnectionRequired &&
                        v.locale.language == "en" &&
                        v.quality >= android.speech.tts.Voice.QUALITY_HIGH
                    }
                    ?.maxByOrNull { it.quality }
                if (best != null) {
                    tts.voice = best
                    DebugLog.i("TTS", "Selected voice: ${best.name} quality=${best.quality}")
                }
                tts.setSpeechRate(1.0f)
                tts.setPitch(1.0f)
            }
        }
    }

    // Speak text using Android on-device TTS. Suspends until the utterance completes
    // or is cancelled. No network required — playback is instant on-device.
    suspend fun speak(text: String) {
        val isReady = ready.await()
        if (!isReady) {
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
                    DebugLog.e("TTS", "Utterance error: $utteranceId")
                    if (cont.isActive) cont.resume(Unit)
                }

                override fun onError(utteranceId: String?, errorCode: Int) {
                    DebugLog.e("TTS", "Utterance error $errorCode: $utteranceId")
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
        tts.stop()
    }

    fun destroy() {
        tts.stop()
        tts.shutdown()
    }
}
