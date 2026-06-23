package com.lordmuffin.jarvisvoice.speech

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import com.lordmuffin.jarvisvoice.AudioDeviceRouter
import com.lordmuffin.jarvisvoice.DebugLog

class AndroidSpeechEngine(private val context: Context) : SpeechEngine {

    companion object {
        private const val CONFIDENCE_THRESHOLD = 0.65f
        private const val SILENCE_TIMEOUT_MS   = 2_500L
        // When holdMode=true, pass a large silence window so the recognizer never
        // auto-terminates on a pause. The user stops explicitly via stopListening().
        private const val SILENCE_HOLDMODE_MS  = 5 * 60 * 1000L  // 5 min
    }

    private var recognizer: SpeechRecognizer? = null
    private val mainHandler = Handler(Looper.getMainLooper())
    private var onPartialCallback: ((String) -> Unit)? = null
    private var onFinalCallback: ((String) -> Unit)? = null
    private var onErrorCallback: ((Int) -> Unit)? = null
    private val deviceRouter = AudioDeviceRouter(context)
    private var scoStarted = false
    private var holdModeActive = false

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
        holdModeActive = holdMode

        // Start Bluetooth SCO if user selected a Bluetooth input device
        val preferredDevice = deviceRouter.getPreferredDevice()
        if (preferredDevice != null && deviceRouter.isBluetoothSco(preferredDevice)) {
            deviceRouter.startBluetoothSco()
            scoStarted = true
            DebugLog.i("AndroidSTT", "Bluetooth SCO started for ${deviceRouter.deviceLabel(preferredDevice)}")
        }

        val silenceMs = if (holdMode) SILENCE_HOLDMODE_MS else SILENCE_TIMEOUT_MS
        DebugLog.i("AndroidSTT", "startListening holdMode=$holdMode silenceMs=$silenceMs")

        mainHandler.post {
            if (recognizer == null) createRecognizer()
            val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
                putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
                putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
                putExtra(
                    RecognizerIntent.EXTRA_SPEECH_INPUT_COMPLETE_SILENCE_LENGTH_MILLIS,
                    silenceMs
                )
                putExtra(
                    RecognizerIntent.EXTRA_SPEECH_INPUT_POSSIBLY_COMPLETE_SILENCE_LENGTH_MILLIS,
                    silenceMs
                )
                putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1)
            }
            recognizer?.startListening(intent)
            // In holdMode, don't post the fallback watchdog — the user stops explicitly.
            if (!holdMode) {
                mainHandler.postDelayed(silenceRunnable, silenceMs + 2000)
            }
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
        if (scoStarted) {
            deviceRouter.stopBluetoothSco()
            scoStarted = false
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
            if (scoStarted) {
                deviceRouter.stopBluetoothSco()
                scoStarted = false
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
