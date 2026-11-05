package com.lordmuffin.jarvisvoice.screenshots

import android.os.SystemClock
import android.view.MotionEvent
import android.view.View
import androidx.compose.foundation.layout.Box
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.layout.onGloballyPositioned
import androidx.compose.ui.node.RootForTest
import androidx.compose.ui.platform.ComposeView
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.SemanticsNode
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.semantics.getOrNull
import app.cash.paparazzi.Paparazzi
import app.cash.paparazzi.Snapshot
import app.cash.paparazzi.SnapshotHandler
import java.lang.reflect.Proxy
import com.lordmuffin.jarvisvoice.R
import com.lordmuffin.jarvisvoice.live.ask.AskUi
import com.lordmuffin.jarvisvoice.ui.compose.JarvisTheme
import com.lordmuffin.jarvisvoice.ui.compose.RecordActions
import com.lordmuffin.jarvisvoice.ui.compose.RecordScreen
import com.lordmuffin.jarvisvoice.ui.compose.RecordUi
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.junit.runners.Parameterized

/**
 * The hold-to-ask button, measured rather than looked at: it must not move or resize as the ask
 * state changes, and a press held across a state change must last until the finger lifts.
 * Rendered by Paparazzi with nothing recorded; the screenshots of these states are in [RecordScreenTest].
 */
@RunWith(Parameterized::class)
class AskButtonTest(private val display: Display) {

    @get:Rule val paparazzi = Paparazzi(
        deviceConfig = display.config(dark = false),
        theme = "Theme.Jarvis",
        showSystemUi = false,
        snapshotHandler = Discard,
    )

    private val longReply = "Priya runs the survey and it is due on Friday; Sam owns the follow-up with the vendor, " +
        "and the budget review moved to next Tuesday after the offsite, so the slides need to be ready by Monday."

    private val states: Map<String, RecordUi> = mapOf(
        "ready" to Fixtures.recording.copy(ask = AskUi.Ready),
        "listening" to Fixtures.recording.copy(ask = AskUi.Listening),
        "waiting" to Fixtures.recording.copy(ask = AskUi.Waiting),
        "speaking" to Fixtures.recording.copy(ask = AskUi.Speaking(longReply)),
        "answered long" to Fixtures.recording.copy(ask = AskUi.Answered(longReply, spoken = false, why = AskUi.Why.ROUTE)),
        "answered short" to Fixtures.recording.copy(ask = AskUi.Answered("Friday.", spoken = true)),
        "muted" to Fixtures.recording.copy(ask = AskUi.Ready, askMuted = true),
    )

    @Test fun theButtonHasTheSameBoundsInEveryAskState() {
        val bounds = states.mapValues { (_, ui) -> measure(ui) }
        val ready = bounds.getValue("ready")
        bounds.forEach { (state, rect) -> assertEquals("ask button bounds when $state", ready, rect) }
    }

    /** The press clears a long answer: the button must not slide out from under the finger. */
    @Test fun aPressThatClearsALongAnswerLastsUntilRelease() =
        holdAcross(Fixtures.recording.copy(ask = AskUi.Answered(longReply, spoken = false, why = AskUi.Why.ROUTE)), freshCallback = false)

    /** The caller hands over a new callback as the press recomposes it: the gesture must not restart. */
    @Test fun aPressThatGetsANewCallbackLastsUntilRelease() =
        holdAcross(Fixtures.recording.copy(ask = AskUi.Ready), freshCallback = true)

    /**
     * Press, let the press change the state the way the service does (to listening, with a new
     * [RecordActions] when [freshCallback]), wiggle the finger, then lift it.
     */
    private fun holdAcross(start: RecordUi, freshCallback: Boolean) {
        val events = mutableListOf<String>()
        var ui by mutableStateOf(start)
        var actions by mutableStateOf(RecordActions())
        fun fresh(): RecordActions = RecordActions(onAsk = { down ->
            events += "hold($down)"
            if (down == true) {
                ui = ui.copy(ask = AskUi.Listening)
                if (freshCallback) actions = fresh()
            }
        })
        actions = fresh()
        val view = ComposeView(paparazzi.context)
        view.setContent {
            val host = LocalView.current
            JarvisTheme(darkTheme = false) { RecordScreen(ui, actions) }
            LaunchedEffect(Unit) {
                repeat(2) { withFrameNanos { } }
                val at = askButton(host).center
                val t0 = SystemClock.uptimeMillis()
                host.touch(MotionEvent.ACTION_DOWN, t0, at.x, at.y)
                events += "down"
                repeat(FRAMES) { withFrameNanos { } }
                host.touch(MotionEvent.ACTION_MOVE, t0, at.x + 1f, at.y + 1f)
                repeat(FRAMES) { withFrameNanos { } }
                events += "up"
                host.touch(MotionEvent.ACTION_UP, t0, at.x + 1f, at.y + 1f)
                repeat(2) { withFrameNanos { } }
                events += "done"
            }
        }
        paparazzi.gif(view, "hold", start = 0, end = 1_000, fps = 60)
        assertEquals(listOf("down", "hold(true)", "up", "hold(false)", "done"), events)
    }

    private fun measure(ui: RecordUi): Rect {
        var rect: Rect? = null
        paparazzi.snapshot {
            val host = LocalView.current
            Box(Modifier.onGloballyPositioned { rect = askButton(host) }) {
                JarvisTheme(darkTheme = false) { RecordScreen(ui, RecordActions()) }
            }
        }
        return checkNotNull(rect) { "the ask button was never laid out" }
    }

    /** The bounds of the button whose label is one of the ask labels. */
    private fun askButton(host: View): Rect {
        val labels = listOf(R.string.live_ask_hold, R.string.live_ask_listening, R.string.live_ask_waiting)
            .map { paparazzi.context.getString(it) }
        val root = (host as RootForTest).semanticsOwner.unmergedRootSemanticsNode
        val label = root.descendants().first { n -> n.config.getOrNull(SemanticsProperties.Text)?.any { it.text in labels } == true }
        var node: SemanticsNode? = label
        while (node != null && node.config.getOrNull(SemanticsProperties.Role) != Role.Button) node = node.parent
        return checkNotNull(node) { "no button around the ask label" }.boundsInRoot
    }

    private fun SemanticsNode.descendants(): Sequence<SemanticsNode> = sequence {
        yield(this@descendants)
        children.forEach { yieldAll(it.descendants()) }
    }

    private fun View.touch(action: Int, downTime: Long, x: Float, y: Float) {
        val e = MotionEvent.obtain(downTime, SystemClock.uptimeMillis(), action, x, y, 0)
        dispatchTouchEvent(e)
        e.recycle()
    }

    companion object {
        private const val FRAMES = 6

        /** Draws nothing to disk: a proxy, since java.awt (the frame type) is not on the test compile classpath. */
        private val Discard: SnapshotHandler = proxy(SnapshotHandler::class.java) { proxy(SnapshotHandler.FrameHandler::class.java) { Unit } }

        private fun <T> proxy(type: Class<T>, answer: () -> Any): T = type.cast(
            Proxy.newProxyInstance(type.classLoader, arrayOf(type)) { _, method, _ -> if (method.name == "close") null else answer() },
        )

        @JvmStatic @Parameterized.Parameters(name = "{0}")
        fun displays(): List<Display> = Display.values().toList()
    }
}
