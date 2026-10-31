package com.lordmuffin.jarvisvoice.screenshots

import com.lordmuffin.jarvisvoice.live.ConnectionKind
import com.lordmuffin.jarvisvoice.ui.compose.JarvisTheme
import com.lordmuffin.jarvisvoice.ui.compose.RecentUi
import com.lordmuffin.jarvisvoice.ui.compose.RecordActions
import com.lordmuffin.jarvisvoice.ui.compose.RecordUi
import com.lordmuffin.jarvisvoice.ui.compose.RecordScreen
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.junit.runners.Parameterized

/** The Record screen, on both displays in light and dark. These are the regression baselines. */
@RunWith(Parameterized::class)
class RecordScreenTest(private val variant: Variant) {

    @get:Rule val paparazzi = paparazziFor(variant)

    private fun shot(name: String, ui: RecordUi) = paparazzi.snapshot(name = "${name}_${variant.tag}") {
        JarvisTheme(darkTheme = variant.dark) { RecordScreen(ui, RecordActions()) }
    }

    @Test fun idle() = shot("record_idle", Fixtures.idle)
    @Test fun idleNoHistoryYet() = shot("record_idle_empty", Fixtures.idle.copy(pending = emptyList(), recent = RecentUi.Empty))
    @Test fun idleSignedOut() = shot("record_idle_signed_out", Fixtures.idle.copy(pending = emptyList(), recent = RecentUi.SignedOut))
    @Test fun idleLoadingHistory() = shot("record_idle_loading", Fixtures.idle.copy(recent = RecentUi.Loading))
    @Test fun idleHistoryError() = shot("record_idle_error", Fixtures.idle.copy(recent = RecentUi.Error("Couldn't load sessions: HTTP 502")))
    @Test fun recording() = shot("record_recording", Fixtures.recording)
    @Test fun recordingNoSpeechYet() = shot("record_recording_empty", Fixtures.recording.copy(transcript = emptyList(), copilot = emptyList(), levels = Fixtures.levels.take(6)))
    @Test fun recordingReconnecting() = shot(
        "record_recording_reconnecting",
        Fixtures.recording.copy(connection = ConnectionKind.RECONNECTING, connectionDetail = "Reconnecting (attempt 2)", message = "Server ended the session: it restarted."),
    )

    companion object {
        @JvmStatic @Parameterized.Parameters(name = "{0}")
        fun variants(): List<Variant> = Variant.ALL
    }
}
