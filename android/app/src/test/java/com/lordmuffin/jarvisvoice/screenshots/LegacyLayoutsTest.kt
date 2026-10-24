package com.lordmuffin.jarvisvoice.screenshots

import android.view.View
import androidx.annotation.LayoutRes
import com.lordmuffin.jarvisvoice.R
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.junit.runners.Parameterized

/**
 * Screenshots of the screens that are still View/XML (Settings, Chat, Models, Agent, ...), every layout
 * on both displays in light and dark. The regression baseline for them; the migrated Record, History
 * and Detail screens have their own Compose tests. The "before" images in docs/ui-audit/before/ were
 * taken from this class before those screens moved.
 */
@RunWith(Parameterized::class)
class LegacyLayoutsTest(private val variant: Variant) {

    @get:Rule val paparazzi = paparazziFor(variant)

    private fun <T : View> inflate(@LayoutRes id: Int): T = paparazzi.inflate(id)
    private fun shot(name: String, view: View) = paparazzi.snapshot(view, name = "${name}_${variant.tag}")

    @Test fun settings() = shot("settings", inflate(R.layout.activity_settings))
    @Test fun onboarding() = shot("onboarding", inflate(R.layout.activity_onboarding))
    @Test fun dictationHistory() = shot("dictation_history", inflate(R.layout.activity_history))
    @Test fun voiceChat() = shot("voice_chat", inflate(R.layout.activity_voice_chat))
    @Test fun agentTasks() = shot("agent_tasks", inflate(R.layout.activity_agent_tasks))
    @Test fun models() = shot("models", inflate(R.layout.activity_model_manager))
    @Test fun sttModels() = shot("stt_models", inflate(R.layout.activity_stt_model_manager))
    @Test fun dictionary() = shot("dictionary", inflate(R.layout.activity_dictionary))
    @Test fun debugLog() = shot("debug_log", inflate(R.layout.activity_debug_log))

    companion object {
        @JvmStatic @Parameterized.Parameters(name = "{0}")
        fun variants(): List<Variant> = Variant.ALL
    }
}
