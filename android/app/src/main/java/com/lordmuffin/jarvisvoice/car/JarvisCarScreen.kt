package com.lordmuffin.jarvisvoice.car

import android.graphics.drawable.Icon
import android.os.Build
import androidx.annotation.OptIn
import androidx.car.app.CarContext
import androidx.car.app.model.Action
import androidx.car.app.model.ActionStrip
import androidx.car.app.model.CarColor
import androidx.car.app.model.CarText
import androidx.car.app.model.Pane
import androidx.car.app.model.PaneTemplate
import androidx.car.app.model.Row
import androidx.car.app.model.Template
import androidx.car.app.validation.HostApis
import androidx.media3.common.MediaMetadata
import com.lordmuffin.jarvisvoice.JarvisApp
import com.lordmuffin.jarvisvoice.R
import com.lordmuffin.jarvisvoice.chat.ChatStatus
import com.lordmuffin.jarvisvoice.chat.VoiceChatViewModel
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.launch

/**
 * Android Auto car screen showing conversation state with mute/stop/controls.
 * Displays:
 *   - Current status (Idle, Listening, Thinking, Speaking)
 *   - Streaming text from AI response
 *   - Control buttons: Mute, Stop, Interrupt
 */
class JarvisCarScreen(carContext: CarContext) : androidx.car.app.Screen(carContext) {

    private val carScope = CoroutineScope(Dispatchers.Main + SupervisorJob())
    private var currentText = CarText("Jarvis Voice – Ready")
    private var currentStatus = CarText("Idle")
    private var isMuted = false
    private var isSpeaking = false
    private var chatStatus = ChatStatus.IDLE
    private lateinit var viewModel: VoiceChatViewModel

    override fun onGetTemplate(): Template {
        return PaneTemplate.Builder()
            .setTitle(CarText("Jarvis Voice"))
            .setSubtitle(CarText("AI Assistant"))
            .setActionStrip(createActionStrip())
            .setHeader(createHeader())
            .setPane(createConversationPane())
            .build()
    }

    private fun createHeader(): androidx.car.app.model.Header {
        return androidx.car.app.model.Header.Builder()
            .setTitle(CarText("Jarvis Voice"))
            .setSubtitle(CarText("AI Assistant"))
            .setAlwaysShownAction(
                Action.Builder()
                    .setIcon(Icon.createWithResource(carContext, R.drawable.ic_mic))
                    .setOnClickListener { toggleVoice() }
                    .setAccessibilityTitle("Voice Command")
                    .build()
            )
            .build()
    }

    private fun createActionStrip(): ActionStrip {
        val actions = mutableListOf<Action>()

        // Mute / Unmute toggle
        val muteAction = Action.Builder()
            .setIcon(Icon.createWithResource(carContext, R.drawable.ic_mic))
            .setTitle(CarText(if (isMuted) "Unmute" else "Mute"))
            .setOnClickListener {
                isMuted = !isMuted
                onMuteClicked()
                invalidate()
            }
            .setAccessibilityTitle(if (isMuted) "Unmute Jarvis" else "Mute Jarvis")
            .build()
        actions.add(muteAction)

        // Stop / Cancel conversation
        val stopAction = Action.Builder()
            .setIcon(
                Icon.createWithResource(carContext, android.R.drawable.ic_media_pause)
            )
            .setTitle(CarText("Stop"))
            .setOnClickListener {
                onStopClicked()
                invalidate()
            }
            .setAccessibilityTitle("Stop Jarvis")
            .build()
        actions.add(stopAction)

        // Interrupt – barge-in when speaking
        if (isSpeaking) {
            val interruptAction = Action.Builder()
                .setIcon(
                    Icon.createWithResource(
                        carContext,
                        android.R.drawable.ic_menu_close_clear_cancel
                    )
                )
                .setTitle(CarText("Interrupt"))
                .setOnClickListener {
                    onInterruptClicked()
                    invalidate()
                }
                .setAccessibilityTitle("Interrupt Jarvis")
                .build()
            actions.add(interruptAction)
        }

        return ActionStrip.Builder()
            .addAllActions(actions)
            .build()
    }

    private fun createConversationPane(): Pane {
        val rows = mutableListOf<Row>()

        // Current status row
        rows.add(
            Row.Builder()
                .addText(currentStatus)
                .build()
        )

        // Streaming text row
        rows.add(
            Row.Builder()
                .addText(currentText)
                .build()
        )

        // Quick actions row
        val quickActions = listOf(
            "Speak" to "Tap to talk",
            "Listen" to "Listening for voice"
        )

        for ((title, desc) in quickActions) {
            rows.add(
                Row.Builder()
                    .addText(CarText(title))
                    .addText(CarText(desc))
                    .build()
            )
        }

        return Pane.Builder()
            .addAllRows(rows)
            .build()
    }

    // ── Callbacks ─────────────────────────────────────────────────────────────

    private fun onMuteClicked() {
        val app = carContext.applicationContext as? JarvisApp ?: return
        app.chatViewModel?.let { vm ->
            if (isMuted) {
                vm.ttsStop()
            }
        }
    }

    private fun onStopClicked() {
        val app = carContext.applicationContext as? JarvisApp ?: return
        app.chatViewModel?.let { vm ->
            vm.ttsStop()
            vm.cancelActive()
        }
    }

    private fun onInterruptClicked() {
        val app = carContext.applicationContext as? JarvisApp ?: return
        app.chatViewModel?.let { vm ->
            vm.interruptSpeaking()
        }
    }

    private fun toggleVoice() {
        // Toggle voice mode (start/stop listening)
        val app = carContext.applicationContext as? JarvisApp ?: return
        app.chatViewModel?.let { vm ->
            if (vm.status.value == ChatStatus.SPEAKING) {
                vm.interruptSpeaking()
            } else {
                vm.setStatus(ChatStatus.LISTENING)
            }
        }
    }

    // ── Lifecycle ─────────────────────────────────────────────────────────────

    override fun onTemplateRender(template: Template) {
        super.onTemplateRender(template)
        // Start observing ViewModel state when template is rendered
        observeViewModel()
    }

    private fun observeViewModel() {
        val app = carContext.applicationContext as? JarvisApp ?: return
        viewModel = app.chatViewModel ?: return

        carScope.launch {
            viewModel.status.collectLatest { status ->
                chatStatus = status
                currentStatus = CarText(status.name.lowercase().replaceFirstChar { it.uppercase() })
                isSpeaking = status == ChatStatus.SPEAKING
                invalidate()
            }
        }

        carScope.launch {
            viewModel.streamingText.collectLatest { text ->
                currentText = CarText(
                    if (text.length > 200) text.take(200) + "…" else text
                )
                invalidate()
            }
        }
    }

    override fun onDestroy() {
        carScope.cancel()
        super.onDestroy()
    }
}
