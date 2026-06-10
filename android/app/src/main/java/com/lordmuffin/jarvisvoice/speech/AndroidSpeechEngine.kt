package com.lordmuffin.jarvisvoice.speech

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import com.lordmuffin.jarvisvoice.DebugLog

class AndroidSpeechEngine(private val context: Context) : SpeechEngine {

    companion object {
        private const val CONFIDENCE_THRESHOLD = 0.65f
    }

    private var recognizer: SpeechRecognizer? = null
    private val mainHandler = Handler(Looper.getMainLooper())
    private var onPartialCallback: ((String) -> Unit)? = null
    private var onFinalCallback: ((String) -> Unit)? = null
    private var onErrorCallback: ((Int) -> Unit)? = null

    private val silenceTimeoutMs = 10_000L
    private val silenceRunnable = Runnable {
        recognizer?.stopListening()
    }

    init {
        mainHandler.post { createRecognizer() }
    }

    private fun createRecognizer() {
        val canUseOnDevice = SpeechRecognizer.isOnDeviceRecognitionAvailable(context)
        recognizer = if (canUseOnDevice) {
            SpeechRecognizer.createOnDeviceSpeechRecognizer(context)
        } else {
            SpeechRecognizer.createSpeechRecognizer(context)
        }
        recognizer?.setRecognitionListener(listener)
    }

    override fun startListening(
        onPartial: (String) -> Unit,
        onFinal:   (String) -> Unit,
        onError:   (Int)    -> Unit,
        holdMode:  Boolean
    ) {
        onPartialCallback = onPartial
        onFinalCallback = onFinal
        onErrorCallback = onError

        mainHandler.post {
            if (recognizer == null) createRecognizer()
            val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
                putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
                putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
                putExtra(
                    RecognizerIntent.EXTRA_SPEECH_INPUT_COMPLETE_SILENCE_LENGTH_MILLIS,
                    silenceTimeoutMs
                )
                putExtra(
                    RecognizerIntent.EXTRA_SPEECH_INPUT_POSSIBLY_COMPLETE_SILENCE_LENGTH_MILLIS,
                    silenceTimeoutMs
                )
                putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1)
            }
            recognizer?.startListening(intent)
            mainHandler.postDelayed(silenceRunnable, silenceTimeoutMs + 2000)
        }
    }

    override fun stopListening() {
        mainHandler.removeCallbacks(silenceRunnable)
        mainHandler.post { recognizer?.stopListening() }
    }

    override fun destroy() {
        mainHandler.removeCallbacks(silenceRunnable)
        mainHandler.post {
            recognizer?.destroy()
            recognizer = null
        }
    }

    private val listener = object : RecognitionListener {
        override fun onReadyForSpeech(params: Bundle?) {}
        override fun onBeginningOfSpeech() {}
        override fun onRmsChanged(rmsdB: Float) {}
        override fun onBufferReceived(buffer: ByteArray?) {}
        override fun onEndOfSpeech() {
            mainHandler.removeCallbacks(silenceRunnable)
        }
        override fun onError(error: Int) {
            mainHandler.removeCallbacks(silenceRunnable)
            onErrorCallback?.invoke(error)
        }
        override fun onResults(results: Bundle?) {
            mainHandler.removeCallbacks(silenceRunnable)
            val texts      = results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
            val scores     = results?.getFloatArray(SpeechRecognizer.CONFIDENCE_SCORES)
            val text       = texts?.firstOrNull() ?: ""
            val confidence = scores?.firstOrNull() ?: 1f
            if (confidence < CONFIDENCE_THRESHOLD && text.isNotBlank()) {
                DebugLog.i("AndroidSTT", "low-confidence discard (${"%.2f".format(confidence)}): \"${text.take(40)}\"")
                onFinalCallback?.invoke("")
            } else {
                DebugLog.i("AndroidSTT", "result accepted confidence=${"%.2f".format(confidence)} len=${text.length}")
                onFinalCallback?.invoke(text)
            }
            // Recreate recognizer for next use
            recognizer?.destroy()
            recognizer = null
        }
        override fun onPartialResults(partialResults: Bundle?) {
            val partial = partialResults
                ?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                ?.firstOrNull() ?: ""
            if (partial.isNotBlank()) onPartialCallback?.invoke(partial)
        }
        override fun onEvent(eventType: Int, params: Bundle?) {}
    }
}
