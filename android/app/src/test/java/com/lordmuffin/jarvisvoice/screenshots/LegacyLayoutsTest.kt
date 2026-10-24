package com.lordmuffin.jarvisvoice.screenshots

import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.LinearLayout
import android.widget.TextView
import androidx.annotation.LayoutRes
import com.lordmuffin.jarvisvoice.R
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.junit.runners.Parameterized

/**
 * Screenshots of the View/XML screens, every layout on both displays in light and dark. Layouts with
 * lists get fixture rows inflated from the real item layouts. Kept as the regression baseline for
 * the screens that stay on Views, and as the "before" half of docs/ui-audit.md.
 */
@RunWith(Parameterized::class)
class LegacyLayoutsTest(private val variant: Variant) {

    @get:Rule val paparazzi = paparazziFor(variant)

    private fun <T : View> inflate(@LayoutRes id: Int): T = paparazzi.inflate(id)
    private fun View.text(id: Int, value: CharSequence) { findViewById<TextView>(id).text = value }
    private fun View.show(id: Int, visible: Boolean = true) {
        findViewById<View>(id).visibility = if (visible) View.VISIBLE else View.GONE
    }

    private fun rows(parent: ViewGroup, @LayoutRes item: Int, count: Int, bind: View.(Int) -> Unit) {
        val inflater = LayoutInflater.from(parent.context)
        repeat(count) { i -> parent.addView(inflater.inflate(item, parent, false).also { it.bind(i) }) }
    }

    private fun shot(name: String, view: View) = paparazzi.snapshot(view, name = "${name}_${variant.tag}")

    @Test fun recordIdle() {
        val v = inflate<View>(R.layout.activity_live)
        v.text(R.id.tv_live_status, "Server https://live.lab.apj.dev")
        v.text(R.id.tv_dictation_status, "Running")
        v.text(R.id.tv_dictation_last, "Copied · 42 words · 138 wpm · 3 min ago")
        shot("record_idle", v)
    }

    @Test fun recordRecording() {
        val v = inflate<View>(R.layout.activity_live)
        v.show(R.id.group_live_setup, false)
        v.show(R.id.group_live_recording)
        v.text(R.id.tv_live_timer, "12:48")
        v.text(R.id.tv_live_details, "Meeting · Drafts on · 2 markers")
        v.text(R.id.tv_live_status, "Recording · lag 320 ms")
        val list = v.findViewById<androidx.recyclerview.widget.RecyclerView>(R.id.rv_live_transcript)
        v.show(R.id.tv_live_copilot_header)
        v.show(R.id.sv_live_copilot)
        v.text(R.id.tv_live_copilot, "Suggestions\n• Question: What is the rollout date?\n\nActions\n• Send the draft to Priya (owner: Me)")
        v.text(R.id.tv_live_copilot_header, "Copilot ▾")
        // The transcript is a RecyclerView; fixture rows go in a sibling-less stand-in container.
        val holder = LinearLayout(v.context).apply { orientation = LinearLayout.VERTICAL }
        rows(holder, R.layout.item_live_line, 3) { i ->
            text(R.id.tv_line_time, "0${i}:1${i}")
            text(R.id.tv_line_speaker, if (i % 2 == 0) "ME" else "THEM")
            text(R.id.tv_line_text, "Fixture transcript line ${i + 1} with enough words to wrap onto a second line on the cover display.")
        }
        (list.parent as ViewGroup).apply { val at = indexOfChild(list); removeView(list); addView(holder, at) }
        shot("record_recording", v)
    }

    @Test fun historyList() {
        val v = inflate<View>(R.layout.activity_sessions)
        v.text(R.id.tv_sessions_subtitle, "Your Jarvis Live sessions on live.lab.apj.dev")
        val list = v.findViewById<View>(R.id.rv_sessions)
        val holder = LinearLayout(v.context).apply { orientation = LinearLayout.VERTICAL }
        rows(holder, R.layout.item_remote_session, 5) { i ->
            text(R.id.tv_rs_title, "Weekly sync ${i + 1}")
            text(R.id.tv_rs_status, if (i == 0) "Live" else "Done")
            text(R.id.tv_rs_meta, "Oct ${11 - i}, 2026, 9:14 AM · 42:10 · Meeting")
        }
        (list.parent as ViewGroup).apply { val at = indexOfChild(list); removeView(list); addView(holder, at) }
        shot("history_list", v)
    }

    @Test fun historyEmpty() {
        val v = inflate<View>(R.layout.activity_sessions)
        v.show(R.id.group_sessions_message)
        v.text(R.id.tv_sessions_message, v.context.getString(R.string.history_empty))
        v.show(R.id.btn_sessions_retry, false) // MessageAction.NONE hides it
        shot("history_empty", v)
    }

    @Test fun historyError() {
        val v = inflate<View>(R.layout.activity_sessions)
        v.show(R.id.group_sessions_message)
        v.text(R.id.tv_sessions_message, v.context.getString(R.string.history_error, "HTTP 502"))
        shot("history_error", v)
    }

    @Test fun historyLoading() {
        val v = inflate<View>(R.layout.activity_sessions)
        v.show(R.id.pb_sessions)
        shot("history_loading", v)
    }

    @Test fun sessionDetail() {
        val v = inflate<View>(R.layout.activity_session_detail)
        v.text(R.id.tv_detail_title, "Weekly sync")
        v.text(R.id.tv_detail_status, "Done")
        v.text(R.id.tv_detail_meta, "Oct 11, 2026, 9:14 AM · 42:10 · Meeting")
        v.show(R.id.group_detail_tabs)
        v.text(R.id.tv_detail_text, "Summary\n\nThe team agreed to ship the Android refresh behind a flag and review the screenshots on Friday.")
        shot("session_detail", v)
    }

    @Test fun sessionDetailError() {
        val v = inflate<View>(R.layout.activity_session_detail)
        v.show(R.id.group_detail_error)
        v.text(R.id.tv_detail_error, v.context.getString(R.string.detail_error, "HTTP 502"))
        shot("session_detail_error", v)
    }

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
