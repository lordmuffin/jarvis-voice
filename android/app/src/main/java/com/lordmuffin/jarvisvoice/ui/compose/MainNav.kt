package com.lordmuffin.jarvisvoice.ui.compose

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import com.lordmuffin.jarvisvoice.BottomNav.Tab
import com.lordmuffin.jarvisvoice.R
import instrument.Inst

private fun Tab.icon() = when (this) {
    Tab.RECORD -> R.string.nav_icon_record
    Tab.CHAT -> R.string.nav_icon_chat
    Tab.HISTORY -> R.string.nav_icon_history
    Tab.SETTINGS -> R.string.nav_icon_settings
    Tab.MODELS -> R.string.nav_icon_models
    Tab.AGENT -> R.string.nav_icon_agent
}

private fun Tab.label() = when (this) {
    Tab.RECORD -> R.string.nav_label_record
    Tab.CHAT -> R.string.nav_label_chat
    Tab.HISTORY -> R.string.nav_label_history
    Tab.SETTINGS -> R.string.nav_label_settings
    Tab.MODELS -> R.string.nav_label_models
    Tab.AGENT -> R.string.nav_label_agent
}

/** Same order as the View bottom bar. */
val MainTabs = listOf(Tab.RECORD, Tab.CHAT, Tab.HISTORY, Tab.SETTINGS, Tab.MODELS, Tab.AGENT)

/** The 56dp bottom bar (`i-bottombar`): hairline on top, 2dp accent indicator above the current tab. */
@Composable
fun MainNavBar(active: Tab?, onSelect: (Tab) -> Unit, modifier: Modifier = Modifier) {
    val c = Inst.colors
    val sp = InstUi.space
    Column(modifier.fillMaxWidth().background(c.surface)) {
        Hairline(Modifier.fillMaxWidth().height(sp.hairline))
        Row(Modifier.fillMaxWidth().height(sp.bottomBar)) {
            for (tab in MainTabs) {
                val on = tab == active
                Column(
                    Modifier
                        .weight(1f)
                        .fillMaxHeight()
                        .semantics { selected = on }
                        .clickable(role = Role.Tab) { if (!on) onSelect(tab) },
                    horizontalAlignment = Alignment.CenterHorizontally,
                ) {
                    Box(Modifier.fillMaxWidth().height(sp.railEdge - sp.hairline).background(if (on) c.accent else c.surface))
                    Column(
                        Modifier.weight(1f),
                        horizontalAlignment = Alignment.CenterHorizontally,
                        verticalArrangement = Arrangement.Center,
                    ) {
                        InstText(stringResource(tab.icon()), InstUi.type.navIcon, color = if (on) c.text else c.text2)
                        InstText(
                            stringResource(tab.label()),
                            InstUi.type.micro.copy(fontWeight = if (on) FontWeight.Bold else FontWeight.Normal),
                            Modifier.padding(top = sp.s4),
                            color = if (on) c.text else c.text2,
                            maxLines = 1,
                        )
                    }
                }
            }
        }
    }
}

/** The 80dp rail used on the unfolded display: 72dp items, 3dp accent edge on the current one. */
@Composable
fun MainNavRail(active: Tab?, onSelect: (Tab) -> Unit, modifier: Modifier = Modifier) {
    val c = Inst.colors
    val sp = InstUi.space
    Row(modifier.fillMaxHeight().background(c.surface)) {
        Column(Modifier.width(sp.railWidth).padding(top = sp.s8)) {
            for (tab in MainTabs) {
                val on = tab == active
                Row(
                    Modifier
                        .fillMaxWidth()
                        .height(sp.railItem)
                        .semantics { selected = on }
                        .clickable(role = Role.Tab) { if (!on) onSelect(tab) },
                ) {
                    Box(Modifier.width(sp.railEdge).fillMaxHeight().background(if (on) c.accent else c.surface))
                    Column(
                        Modifier.weight(1f).fillMaxHeight(),
                        horizontalAlignment = Alignment.CenterHorizontally,
                        verticalArrangement = Arrangement.Center,
                    ) {
                        InstText(stringResource(tab.icon()), InstUi.type.navIcon, color = if (on) c.text else c.text2)
                        InstText(
                            stringResource(tab.label()),
                            InstUi.type.micro.copy(fontWeight = if (on) FontWeight.Bold else FontWeight.Normal),
                            Modifier.padding(top = sp.s4),
                            color = if (on) c.text else c.text2,
                            maxLines = 1,
                        )
                    }
                }
            }
        }
        Hairline(Modifier.width(sp.hairline).fillMaxHeight())
    }
}
