package com.lordmuffin.jarvisvoice.car

import android.content.Intent
import androidx.car.app.CarContext
import androidx.car.app.Screen
import androidx.car.app.model.Action
import androidx.car.app.model.CarIcon
import androidx.car.app.model.CarText
import androidx.car.app.model.MessageTemplate
import androidx.car.app.model.Template
import androidx.core.graphics.drawable.IconCompat
import androidx.lifecycle.DefaultLifecycleObserver
import androidx.lifecycle.LifecycleOwner
import com.lordmuffin.jarvisvoice.OverlayState
import com.lordmuffin.jarvisvoice.R
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
            muted                            -> str(R.string.car_muted)
            isDone && lastText.isNotBlank()  -> lastText
            else                             -> str(R.string.car_ready)
        }

        val primaryAction = action(
            if (muted) R.string.car_unmute else R.string.car_record,
            if (muted) R.drawable.ic_car_mute else R.drawable.ic_car_mic,
            if (muted) VoiceOverlayService.ACTION_CAR_MUTE else VoiceOverlayService.ACTION_CAR_START,
        )

        return MessageTemplate.Builder(CarText.create(bodyText))
            .setTitle(str(R.string.car_title))
            .setHeaderAction(Action.APP_ICON)
            .addAction(primaryAction)
            .apply { if (!muted) addAction(action(R.string.car_mute, R.drawable.ic_car_mute, VoiceOverlayService.ACTION_CAR_MUTE)) }
            .build()
    }

    private fun buildRecordingTemplate(muted: Boolean): Template =
        MessageTemplate.Builder(CarText.create(str(R.string.car_listening)))
            .setTitle(str(R.string.car_title))
            .setHeaderAction(Action.APP_ICON)
            .addAction(
                action(
                    if (muted) R.string.car_unmute else R.string.car_mute,
                    R.drawable.ic_car_mute,
                    VoiceOverlayService.ACTION_CAR_MUTE,
                )
            )
            .addAction(action(R.string.car_stop, R.drawable.ic_car_stop, VoiceOverlayService.ACTION_CAR_STOP))
            .build()

    private fun buildProcessingTemplate(): Template =
        MessageTemplate.Builder(CarText.create(str(R.string.car_processing)))
            .setTitle(str(R.string.car_title))
            .setHeaderAction(Action.APP_ICON)
            .setLoading(true)
            .build()

    // ── Helpers ──────────────────────────────────────────────────────────────

    private fun sendCarAction(action: String) {
        carContext.startService(
            Intent(carContext, VoiceOverlayService::class.java).setAction(action)
        )
    }

    private fun str(id: Int): String = carContext.getString(id)

    private fun action(titleRes: Int, iconRes: Int, intentAction: String): Action =
        Action.Builder()
            .setTitle(str(titleRes))
            .setIcon(CarIcon.Builder(IconCompat.createWithResource(carContext, iconRes)).build())
            .setOnClickListener { sendCarAction(intentAction) }
            .build()
}
