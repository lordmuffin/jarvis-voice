package com.lordmuffin.jarvisvoice

import android.content.Intent
import android.graphics.Color
import android.graphics.Typeface
import android.view.View
import android.widget.LinearLayout
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import com.lordmuffin.jarvisvoice.live.LiveActivity

object BottomNav {

    enum class Tab { RECORD, HISTORY, CHAT, SETTINGS, MODELS, AGENT }

    fun wire(activity: AppCompatActivity, active: Tab) {
        val root = activity.findViewById<LinearLayout>(R.id.bottom_nav_root) ?: return

        val current = activity.getColor(R.color.inst_text)
        val dimmed = activity.getColor(R.color.inst_text_2)
        val accent = activity.getColor(R.color.inst_accent)

        data class TabDef(val tab: Tab, val tabId: Int, val iconId: Int, val labelId: Int, val indicatorId: Int)

        val tabs = listOf(
            TabDef(Tab.RECORD,   R.id.nav_tab_record,   R.id.nav_icon_record,   R.id.nav_label_record, R.id.nav_indicator_record),
            TabDef(Tab.HISTORY,  R.id.nav_tab_history,  R.id.nav_icon_history,  R.id.nav_label_history, R.id.nav_indicator_history),
            TabDef(Tab.CHAT,     R.id.nav_tab_chat,     R.id.nav_icon_chat,     R.id.nav_label_chat, R.id.nav_indicator_chat),
            TabDef(Tab.SETTINGS, R.id.nav_tab_settings, R.id.nav_icon_settings, R.id.nav_label_settings, R.id.nav_indicator_settings),
            TabDef(Tab.MODELS,   R.id.nav_tab_models,   R.id.nav_icon_models,   R.id.nav_label_models, R.id.nav_indicator_models),
            TabDef(Tab.AGENT,    R.id.nav_tab_agent,    R.id.nav_icon_agent,    R.id.nav_label_agent, R.id.nav_indicator_agent),
        )

        for (t in tabs) {
            // Emoji ignore tint, so the current tab is shown by its bold label and the accent bar above it.
            val isActive = t.tab == active
            val color = if (isActive) current else dimmed
            root.findViewById<TextView>(t.iconId)?.setTextColor(color)
            root.findViewById<TextView>(t.labelId)?.apply {
                setTextColor(color)
                setTypeface(null, if (isActive) Typeface.BOLD else Typeface.NORMAL)
            }
            root.findViewById<View>(t.indicatorId)?.setBackgroundColor(if (isActive) accent else Color.TRANSPARENT)

            if (t.tab != active) {
                root.findViewById<LinearLayout>(t.tabId)?.setOnClickListener {
                    val intent = Intent(activity, when (t.tab) {
                        Tab.RECORD   -> LiveActivity::class.java
                        Tab.HISTORY  -> HistoryActivity::class.java
                        Tab.CHAT     -> VoiceChatActivity::class.java
                        Tab.SETTINGS -> SettingsActivity::class.java
                        Tab.MODELS   -> ModelManagerActivity::class.java
                        Tab.AGENT    -> AgentTaskActivity::class.java
                    })
                    intent.flags = Intent.FLAG_ACTIVITY_REORDER_TO_FRONT
                    activity.startActivity(intent)
                }
            }
        }
    }
}
