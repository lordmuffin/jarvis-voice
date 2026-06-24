package com.lordmuffin.jarvisvoice

import android.content.Intent
import android.widget.LinearLayout
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity

object BottomNav {

    enum class Tab { RECORD, HISTORY, CHAT, SETTINGS, MODELS, AGENT }

    fun wire(activity: AppCompatActivity, active: Tab) {
        val root = activity.findViewById<LinearLayout>(R.id.bottom_nav_root) ?: return

        val accent = activity.getColor(R.color.jv_accent)
        val dimmed = activity.getColor(R.color.jv_text2)

        data class TabDef(val tab: Tab, val tabId: Int, val iconId: Int, val labelId: Int)

        val tabs = listOf(
            TabDef(Tab.RECORD,   R.id.nav_tab_record,   R.id.nav_icon_record,   R.id.nav_label_record),
            TabDef(Tab.HISTORY,  R.id.nav_tab_history,  R.id.nav_icon_history,  R.id.nav_label_history),
            TabDef(Tab.CHAT,     R.id.nav_tab_chat,     R.id.nav_icon_chat,     R.id.nav_label_chat),
            TabDef(Tab.SETTINGS, R.id.nav_tab_settings, R.id.nav_icon_settings, R.id.nav_label_settings),
            TabDef(Tab.MODELS,   R.id.nav_tab_models,   R.id.nav_icon_models,   R.id.nav_label_models),
            TabDef(Tab.AGENT,    R.id.nav_tab_agent,    R.id.nav_icon_agent,    R.id.nav_label_agent),
        )

        for (t in tabs) {
            val color = if (t.tab == active) accent else dimmed
            root.findViewById<TextView>(t.iconId)?.setTextColor(color)
            root.findViewById<TextView>(t.labelId)?.setTextColor(color)

            if (t.tab != active) {
                root.findViewById<LinearLayout>(t.tabId)?.setOnClickListener {
                    val intent = Intent(activity, when (t.tab) {
                        Tab.RECORD   -> VaultCaptureActivity::class.java
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
