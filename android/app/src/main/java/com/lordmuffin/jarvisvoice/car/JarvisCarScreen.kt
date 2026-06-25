package com.lordmuffin.jarvisvoice.car

import android.content.Intent
import androidx.car.app.CarContext
import androidx.car.app.Screen
import androidx.car.app.model.Action
import androidx.car.app.model.CarText
import androidx.car.app.model.MessageTemplate
import androidx.car.app.model.Template
import androidx.lifecycle.DefaultLifecycleObserver
import androidx.lifecycle.LifecycleOwner
import com.lordmuffin.jarvisvoice.OverlayState
import com.lordmuffin.jarvisvoice.VoiceOverlayService
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch

class JarvisCarScreen(carContext: CarContext) : Screen(carContext) {

    private val scope = CoroutineScope(Dispatchers.Main.immediate)

    init {
        lifecycle.addObserver(object : DefaultLifecycleObserver {
            private var job: Job? = null

            override fun onStart(owner: LifecycleOwner) {
                job = scope.launch {
                    launch { VoiceOverlayService.carStateFlow.collect    { invalidate() } }
                    launch { VoiceOverlayService.carLastTextFlow.collect { invalidate() } }
                    launch { VoiceOverlayService.carMutedFlow.collect    { invalidate() } }
                }
            }

            override fun onStop(owner: LifecycleOwner) {
                job?.cancel()
                job = null
            }

            override fun onDestroy(owner: LifecycleOwner) {
                scope.cancel()
            }
        })
    }

    override fun onGetTemplate(): Template {
        val state    = VoiceOverlayService.carStateFlow.value
        val lastText = VoiceOverlayService.carLastTextFlow.value
        val muted    = VoiceOverlayService.carMutedFlow.value

        return when (state) {
            OverlayState.PROCESSING -> buildProcessingTemplate()
            OverlayState.RECORDING  -> buildRecordingTemplate(muted)
            else                    -> buildIdleTemplate(lastText, muted, state == OverlayState.DONE)
        }
    }

    // ── Template builders ────────────────────────────────────────────────────

    private fun buildIdleTemplate(lastText: String, muted: Boolean, isDone: Boolean): Template {
        val bodyText = when {
            muted                            -> "Jarvis is muted.\nTap Unmute to resume."
            isDone && lastText.isNotBlank()  -> lastText
            else                             -> "Tap Record to speak to Jarvis."
        }

        val primaryAction = Action.Builder()
            .setTitle(if (muted) "Unmute" else "Record")
            .setOnClickListener {
                if (muted) sendCarAction(VoiceOverlayService.ACTION_CAR_MUTE)
                else       sendCarAction(VoiceOverlayService.ACTION_CAR_START)
            }
            .build()

        val muteAction = Action.Builder()
            .setTitle("Mute")
            .setOnClickListener { sendCarAction(VoiceOverlayService.ACTION_CAR_MUTE) }
            .build()

        return MessageTemplate.Builder(CarText.create(bodyText))
            .setTitle("Jarvis")
            .setHeaderAction(Action.APP_ICON)
            .addAction(primaryAction)
            .apply { if (!muted) addAction(muteAction) }
            .build()
    }

    private fun buildRecordingTemplate(muted: Boolean): Template {
        val stopAction = Action.Builder()
            .setTitle("Stop")
            .setOnClickListener { sendCarAction(VoiceOverlayService.ACTION_CAR_STOP) }
            .build()

        val muteAction = Action.Builder()
            .setTitle(if (muted) "Unmute" else "Mute")
            .setOnClickListener { sendCarAction(VoiceOverlayService.ACTION_CAR_MUTE) }
            .build()

        return MessageTemplate.Builder(CarText.create("Listening…"))
            .setTitle("Jarvis")
            .setHeaderAction(Action.APP_ICON)
            .addAction(stopAction)
            .addAction(muteAction)
            .build()
    }

    private fun buildProcessingTemplate(): Template =
        MessageTemplate.Builder(CarText.create("Processing…"))
            .setTitle("Jarvis")
            .setHeaderAction(Action.APP_ICON)
            .setLoading(true)
            .build()

    // ── Helpers ──────────────────────────────────────────────────────────────

    private fun sendCarAction(action: String) {
        carContext.startService(
            Intent(carContext, VoiceOverlayService::class.java).setAction(action)
        )
    }
}
