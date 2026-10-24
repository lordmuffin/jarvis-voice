package com.lordmuffin.jarvisvoice.ui.compose

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.booleanResource
import androidx.compose.ui.res.dimensionResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.style.TextOverflow
import com.lordmuffin.jarvisvoice.BottomNav
import com.lordmuffin.jarvisvoice.R
import com.lordmuffin.jarvisvoice.live.ConnectionKind
import com.lordmuffin.jarvisvoice.live.ServerChannel
import com.lordmuffin.jarvisvoice.live.protocol.SessionMode
import com.lordmuffin.jarvisvoice.ui.label
import instrument.Inst

enum class RecordPhase { IDLE, STARTING, RECORDING, STOPPING }

/** Which colour a state word takes. State colours mean state only and always come with the word. */
enum class Tone { Neutral, Accent, Success, Warning, Error }

@Composable
fun Tone.color() = when (this) {
    Tone.Neutral -> Inst.colors.text2
    Tone.Accent -> Inst.colors.accent
    Tone.Success -> Inst.colors.success
    Tone.Warning -> Inst.colors.warning
    Tone.Error -> Inst.colors.error
}

data class PendingUi(val id: String, val title: String?, val state: String, val tone: Tone, val canUpload: Boolean)

data class RecentRow(val id: String, val title: String, val status: String, val statusTone: Tone, val meta: String)

/** What the "Recent sessions" block on the Record page shows. */
sealed interface RecentUi {
    data object Loading : RecentUi
    data object SignedOut : RecentUi
    data object Expired : RecentUi
    data object Empty : RecentUi
    data class Error(val reason: String) : RecentUi
    data class Items(val rows: List<RecentRow>, val refreshing: Boolean = false) : RecentUi
}

data class CopilotBlock(val title: String, val items: List<String>)

data class DictationUi(val running: Boolean, val last: String)

/** Everything the Record screen draws. Built by `LiveActivity` from the service's state; no logic lives here. */
data class RecordUi(
    val phase: RecordPhase = RecordPhase.IDLE,
    val serverLine: String = "",
    val channel: ServerChannel? = null,
    val connection: ConnectionKind = ConnectionKind.IDLE,
    val connectionDetail: String = "",
    val copilotOffline: Boolean = false,
    val message: String? = null,
    val dictation: DictationUi = DictationUi(false, ""),
    val mode: SessionMode = SessionMode.SOLO,
    val title: String = "",
    val localOnly: Boolean = false,
    val networkLabel: String? = null,
    val pending: List<PendingUi> = emptyList(),
    val recent: RecentUi = RecentUi.Loading,
    val timer: String = "",
    val levels: List<Float> = emptyList(),
    val details: String = "",
    val sessionTitle: String = "",
    val transcript: List<FeedLine> = emptyList(),
    val copilot: List<CopilotBlock> = emptyList(),
    val copilotExpanded: Boolean = true,
)

class RecordActions(
    val onStart: () -> Unit = {},
    val onStop: () -> Unit = {},
    val onMarker: () -> Unit = {},
    val onHistory: () -> Unit = {},
    val onOpenSession: (String) -> Unit = {},
    val onModeChange: (SessionMode) -> Unit = {},
    val onTitleChange: (String) -> Unit = {},
    val onLocalOnlyChange: (Boolean) -> Unit = {},
    val onDictation: () -> Unit = {},
    val onDismissMessage: () -> Unit = {},
    val onToggleCopilot: () -> Unit = {},
    val onUpload: (String) -> Unit = {},
    val onDelete: (String) -> Unit = {},
    val onRetryRecent: () -> Unit = {},
    val onOpenSettings: () -> Unit = {},
    val onNav: (BottomNav.Tab) -> Unit = {},
)

/** The Record tab: new session, pending uploads, recent history and the live feed, on one page. */
@Composable
fun RecordScreen(ui: RecordUi, actions: RecordActions, modifier: Modifier = Modifier) {
    val c = Inst.colors
    val wide = booleanResource(R.bool.is_wide)
    Column(modifier.fillMaxSize().background(c.bg)) {
        InstAppBar(
            title = stringResource(R.string.live_title),
            channel = ui.channel,
            actions = {
                InstButton(stringResource(R.string.live_history), actions.onHistory, kind = ButtonKind.Quiet)
            },
        )
        if (!ui.message.isNullOrBlank()) MessageBanner(ui.message, actions.onDismissMessage)
        if (wide) {
            Row(Modifier.weight(1f).fillMaxWidth()) {
                MainNavRail(BottomNav.Tab.RECORD, actions.onNav)
                if (ui.phase == RecordPhase.IDLE) IdleWide(ui, actions, Modifier.weight(1f)) else RecordingWide(ui, actions, Modifier.weight(1f))
            }
        } else {
            Box(Modifier.weight(1f).fillMaxWidth()) {
                if (ui.phase == RecordPhase.IDLE) IdleNarrow(ui, actions) else RecordingNarrow(ui, actions)
            }
            MainNavBar(BottomNav.Tab.RECORD, actions.onNav)
        }
    }
}

@Composable
private fun MessageBanner(text: String, onDismiss: () -> Unit) {
    val c = Inst.colors
    val sp = InstUi.space
    Row(
        Modifier
            .fillMaxWidth()
            .background(c.warningTint)
            .heightIn(min = sp.touchTarget)
            .clickable(role = Role.Button, onClick = onDismiss)
            .padding(horizontal = sp.s12, vertical = sp.s8),
        verticalAlignment = Alignment.CenterVertically,
    ) { InstText(text, InstUi.type.body, color = c.warning) }
}

// region Idle

@Composable
private fun ServerRow(ui: RecordUi, modifier: Modifier = Modifier) {
    val sp = InstUi.space
    Row(modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(sp.s8)) {
        InstText(ui.serverLine, InstUi.type.caption, Modifier.weight(1f), maxLines = 2, overflow = TextOverflow.Ellipsis)
        if (ui.copilotOffline) InstChip(stringResource(R.string.conn_copilot_offline), Inst.colors.warning)
        ConnectionChipUi(ui.connection)
    }
}

@Composable
fun ConnectionChipUi(kind: ConnectionKind, modifier: Modifier = Modifier) {
    val c = Inst.colors
    val color = when (kind) {
        ConnectionKind.CONNECTED -> c.accent
        ConnectionKind.RECONNECTING, ConnectionKind.DEGRADED -> c.warning
        ConnectionKind.FAILED -> c.error
        ConnectionKind.LOCAL_ONLY -> c.text
        ConnectionKind.IDLE, ConnectionKind.CONNECTING, ConnectionKind.OFFLINE -> c.text2
    }
    InstChip(stringResource(kind.label), color, modifier)
}

@Composable
private fun DictationRow(d: DictationUi, onClick: () -> Unit) {
    val c = Inst.colors
    val sp = InstUi.space
    Row(
        Modifier
            .fillMaxWidth()
            .background(c.surface)
            .heightIn(min = sp.touchTarget)
            .clickable(role = Role.Button, onClick = onClick)
            .padding(sp.s12),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(sp.s12),
    ) {
        Box(Modifier.size(sp.dotLive).background(if (d.running) c.accent else c.control, InstUi.shapes.round))
        Column(Modifier.weight(1f)) {
            Row(horizontalArrangement = Arrangement.spacedBy(sp.s8), verticalAlignment = Alignment.CenterVertically) {
                InstText(stringResource(R.string.home_dictation_title), InstUi.type.bodyStrong)
                InstText(
                    stringResource(if (d.running) R.string.home_dictation_running else R.string.home_dictation_stopped),
                    InstUi.type.caption, color = if (d.running) c.accent else c.text2,
                )
            }
            InstText(d.last, InstUi.type.meta)
        }
        InstText(stringResource(R.string.home_dictation_open), InstUi.type.caption)
    }
}

@Composable
private fun NewSessionBlock(ui: RecordUi, actions: RecordActions) {
    val sp = InstUi.space
    InstBlock {
        Column(Modifier.fillMaxWidth().padding(sp.s12), verticalArrangement = Arrangement.spacedBy(sp.s8)) {
            InstSegmented(
                options = listOf(SessionMode.SOLO to stringResource(R.string.live_mode_solo), SessionMode.MEETING to stringResource(R.string.live_mode_meeting)),
                selected = ui.mode, onSelect = actions.onModeChange,
            )
            InstTextField(ui.title, actions.onTitleChange, stringResource(R.string.live_title_hint))
            InstSwitchRow(stringResource(R.string.live_local_only), ui.localOnly, actions.onLocalOnlyChange)
            if (ui.networkLabel != null) InstText(ui.networkLabel, InstUi.type.caption)
            InstButton(
                stringResource(R.string.live_start), actions.onStart,
                Modifier.fillMaxWidth(), kind = ButtonKind.Primary, size = ButtonSize.Lg,
                enabled = ui.phase == RecordPhase.IDLE,
            )
        }
    }
}

@Composable
private fun PendingBlock(pending: List<PendingUi>, actions: RecordActions) {
    if (pending.isEmpty()) return
    val sp = InstUi.space
    Column(verticalArrangement = Arrangement.spacedBy(sp.s4)) {
        SectionLabel("${stringResource(R.string.live_pending)} · ${pending.size}")
        for (p in pending) {
            val name = p.title ?: stringResource(R.string.history_untitled)
            InstBlock {
                Row(
                    Modifier.fillMaxWidth().heightIn(min = sp.row + sp.s24).padding(start = sp.s12, top = sp.s8, bottom = sp.s8, end = sp.s4),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(sp.s4 / 2)) {
                        InstText(name, InstUi.type.bodyStrong, maxLines = 1, overflow = TextOverflow.Ellipsis)
                        InstText(p.state, InstUi.type.meta, color = p.tone.color())
                    }
                    if (p.canUpload) InstButton(stringResource(R.string.ui_pending_upload), { actions.onUpload(p.id) })
                    InstButton(
                        stringResource(R.string.live_delete), { actions.onDelete(p.id) },
                        kind = ButtonKind.Quiet, contentDescription = stringResource(R.string.ui_pending_delete_a11y, name),
                    )
                }
            }
        }
    }
}

/** The last few server sessions, so history is on the page that starts a new one. */
@Composable
fun RecentSessionsBlock(recent: RecentUi, onOpen: (String) -> Unit, onSeeAll: () -> Unit, onRetry: () -> Unit, onOpenSettings: () -> Unit) {
    val sp = InstUi.space
    Column(verticalArrangement = Arrangement.spacedBy(sp.s4)) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            SectionLabel(stringResource(R.string.ui_recent_sessions), Modifier.weight(1f))
            if (recent is RecentUi.Items) InstButton(stringResource(R.string.ui_recent_see_all), onSeeAll, kind = ButtonKind.Quiet)
        }
        when (recent) {
            RecentUi.Loading -> repeat(3) { SkeletonBlock(Modifier.fillMaxWidth().height(sp.row + sp.s16)) }
            RecentUi.SignedOut -> StatePanel(
                stringResource(R.string.ui_history_signin_title), stringResource(R.string.history_not_signed_in),
                tone = StateTone.Warning, actionText = stringResource(R.string.ui_history_open_settings), primaryAction = true, onAction = onOpenSettings,
            )
            RecentUi.Expired -> StatePanel(
                stringResource(R.string.ui_history_expired_title), stringResource(R.string.ui_history_signin_line),
                tone = StateTone.Warning, actionText = stringResource(R.string.ui_history_open_settings), primaryAction = true, onAction = onOpenSettings,
            )
            RecentUi.Empty -> StatePanel(stringResource(R.string.ui_history_empty_title), stringResource(R.string.ui_history_empty_line))
            is RecentUi.Error -> StatePanel(
                stringResource(R.string.ui_history_error_title), recent.reason,
                tone = StateTone.Error, actionText = stringResource(R.string.history_retry), onAction = onRetry,
            )
            is RecentUi.Items -> Column(Modifier.refreshing(recent.refreshing), verticalArrangement = Arrangement.spacedBy(sp.s4)) {
                recent.rows.forEach { SessionRow(it, onClick = { onOpen(it.id) }) }
            }
        }
    }
}

/** A session in a list: title and meta on the left, state word on the right (`j-srow`). */
@Composable
fun SessionRow(row: RecentRow, onClick: () -> Unit, modifier: Modifier = Modifier, selected: Boolean = false) {
    val c = Inst.colors
    val sp = InstUi.space
    Row(
        modifier
            .fillMaxWidth()
            .background(if (selected) c.accentTint else c.surface)
            .heightIn(min = sp.touchTarget + sp.s8)
            .clickable(role = Role.Button, onClick = onClick)
            .padding(horizontal = sp.s12, vertical = sp.s8),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(sp.s8),
    ) {
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(sp.s4 / 2)) {
            InstText(row.title, InstUi.type.bodyStrong, maxLines = 1, overflow = TextOverflow.Ellipsis)
            InstText(row.meta, InstUi.type.meta, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
        InstChip(row.status, row.statusTone.color())
    }
}

@Composable
private fun IdleNarrow(ui: RecordUi, actions: RecordActions) {
    val sp = InstUi.space
    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(sp.s16),
        verticalArrangement = Arrangement.spacedBy(sp.s12),
    ) {
        ServerRow(ui)
        DictationRow(ui.dictation, actions.onDictation)
        NewSessionBlock(ui, actions)
        PendingBlock(ui.pending, actions)
        RecentSessionsBlock(ui.recent, actions.onOpenSession, actions.onHistory, actions.onRetryRecent, actions.onOpenSettings)
    }
}

@Composable
private fun IdleWide(ui: RecordUi, actions: RecordActions, modifier: Modifier) {
    val sp = InstUi.space
    Row(modifier.fillMaxSize()) {
        Column(
            Modifier.width(sp.sessionPane + sp.s48).fillMaxHeight().verticalScroll(rememberScrollState()).padding(sp.s16),
            verticalArrangement = Arrangement.spacedBy(sp.s12),
        ) {
            ServerRow(ui)
            DictationRow(ui.dictation, actions.onDictation)
            NewSessionBlock(ui, actions)
            PendingBlock(ui.pending, actions)
        }
        Hairline(Modifier.width(sp.hairline).fillMaxHeight())
        Column(
            Modifier.weight(1f).fillMaxHeight().verticalScroll(rememberScrollState()).padding(sp.s16),
        ) {
            RecentSessionsBlock(ui.recent, actions.onOpenSession, actions.onHistory, actions.onRetryRecent, actions.onOpenSettings)
        }
    }
}

// endregion

// region Recording

@Composable
private fun SessionHeader(ui: RecordUi, modifier: Modifier = Modifier) {
    val c = Inst.colors
    val sp = InstUi.space
    Column(modifier.fillMaxWidth().background(c.surface).padding(horizontal = sp.s16, vertical = sp.s12), verticalArrangement = Arrangement.spacedBy(sp.s4)) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(sp.s8)) {
            InstText(
                ui.sessionTitle.ifBlank { ui.serverLine }, InstUi.type.bodyStrong, Modifier.weight(1f),
                maxLines = 1, overflow = TextOverflow.Ellipsis,
            )
            if (ui.copilotOffline) InstChip(stringResource(R.string.conn_copilot_offline), c.warning)
            ConnectionChipUi(ui.connection)
        }
        if (ui.details.isNotBlank()) InstText(ui.details, InstUi.type.meta)
        if (ui.connectionDetail.isNotBlank()) InstText(ui.connectionDetail, InstUi.type.metaMono)
    }
}

@Composable
private fun CopilotPanel(ui: RecordUi, collapsible: Boolean, onToggle: () -> Unit, modifier: Modifier = Modifier) {
    if (ui.copilot.isEmpty()) return
    val c = Inst.colors
    val sp = InstUi.space
    Column(modifier.fillMaxWidth().background(c.surface)) {
        val glyph = stringResource(if (ui.copilotExpanded) R.string.livesess_copilot_expanded else R.string.livesess_copilot_collapsed)
        Row(
            Modifier.fillMaxWidth().heightIn(min = sp.touchTarget).clickable(enabled = collapsible, role = Role.Button, onClick = onToggle).padding(horizontal = sp.s16),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            SectionLabel("${stringResource(R.string.live_copilot)} ${if (collapsible) glyph else ""}".trim(), color = c.text)
        }
        if (ui.copilotExpanded || !collapsible) {
            Column(
                Modifier.fillMaxWidth().heightIn(max = dimensionResource(R.dimen.app_live_copilot_height)).verticalScroll(rememberScrollState()).padding(horizontal = sp.s16).padding(bottom = sp.s12),
                verticalArrangement = Arrangement.spacedBy(sp.s8),
            ) {
                for (block in ui.copilot) {
                    SectionLabel(block.title)
                    for (item in block.items) InstText("• $item", InstUi.type.body)
                }
            }
        }
        Hairline(Modifier.fillMaxWidth().height(sp.hairline))
    }
}

@Composable
private fun LiveFeed(ui: RecordUi, modifier: Modifier = Modifier) {
    val sp = InstUi.space
    Column(modifier) {
        TranscriptFeed(
            ui.transcript, Modifier.weight(1f).fillMaxWidth(),
            contentPadding = PaddingValues(horizontal = sp.s16, vertical = sp.s8),
            empty = {
                StatePanel(
                    stringResource(R.string.ui_transcript_empty_title), stringResource(R.string.live_transcript_empty),
                    Modifier.padding(sp.s16),
                )
            },
        )
        Row(Modifier.fillMaxWidth().heightIn(min = sp.controlSm).padding(horizontal = sp.s16), verticalAlignment = Alignment.CenterVertically) {
            InstText(stringResource(R.string.ui_following_live), InstUi.type.meta)
        }
    }
}

@Composable
private fun RecordControls(ui: RecordUi, actions: RecordActions, modifier: Modifier = Modifier, stacked: Boolean) {
    val c = Inst.colors
    val sp = InstUi.space
    val stop = ui.phase != RecordPhase.RECORDING && ui.phase != RecordPhase.STARTING
    Column(modifier.fillMaxWidth().background(c.surface), verticalArrangement = Arrangement.spacedBy(sp.s8)) {
        Hairline(Modifier.fillMaxWidth().height(sp.hairline))
        Column(Modifier.padding(horizontal = sp.s16).padding(bottom = sp.s12), verticalArrangement = Arrangement.spacedBy(sp.s8)) {
            if (stacked) {
                RecPill(ui.timer, ui.levels, Modifier.fillMaxWidth())
                Row(horizontalArrangement = Arrangement.spacedBy(sp.s8)) {
                    InstButton(stringResource(R.string.live_marker), actions.onMarker, Modifier.weight(1f), size = ButtonSize.Lg)
                    InstButton(stringResource(R.string.live_stop), actions.onStop, Modifier.weight(1f), kind = ButtonKind.Danger, size = ButtonSize.Lg, enabled = !stop)
                }
            } else {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(sp.s8)) {
                    RecPill(ui.timer, ui.levels, Modifier.weight(1f))
                    InstButton(stringResource(R.string.live_marker), actions.onMarker, size = ButtonSize.Lg)
                    InstButton(stringResource(R.string.live_stop), actions.onStop, kind = ButtonKind.Danger, size = ButtonSize.Lg, enabled = !stop)
                }
            }
        }
    }
}

@Composable
private fun RecordingNarrow(ui: RecordUi, actions: RecordActions) {
    Column(Modifier.fillMaxSize()) {
        SessionHeader(ui)
        CopilotPanel(ui, collapsible = true, onToggle = actions.onToggleCopilot)
        LiveFeed(ui, Modifier.weight(1f))
        RecordControls(ui, actions, stacked = false)
    }
}

@Composable
private fun RecordingWide(ui: RecordUi, actions: RecordActions, modifier: Modifier) {
    val sp = InstUi.space
    Row(modifier.fillMaxSize()) {
        Column(Modifier.width(sp.sessionPane).fillMaxHeight()) {
            SessionHeader(ui)
            Box(Modifier.weight(1f))
            RecordControls(ui, actions, stacked = true)
        }
        Hairline(Modifier.width(sp.hairline).fillMaxHeight())
        Column(Modifier.weight(1f).fillMaxHeight()) {
            CopilotPanel(ui, collapsible = false, onToggle = {})
            LiveFeed(ui, Modifier.weight(1f))
        }
    }
}

// endregion

