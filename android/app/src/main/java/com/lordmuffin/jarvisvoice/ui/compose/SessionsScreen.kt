package com.lordmuffin.jarvisvoice.ui.compose

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.booleanResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import com.lordmuffin.jarvisvoice.BottomNav
import com.lordmuffin.jarvisvoice.R
import com.lordmuffin.jarvisvoice.live.ServerChannel
import instrument.Inst
import kotlinx.coroutines.flow.distinctUntilChanged

enum class ListFooter { NONE, MORE, LOADING, ERROR }

data class SessionsUi(
    val subtitle: String = "",
    val channel: ServerChannel? = null,
    val list: RecentUi = RecentUi.Loading,
    val footer: ListFooter = ListFooter.NONE,
    val selectedId: String? = null,
)

class SessionsActions(
    val onOpen: (RecentRow) -> Unit = {},
    val onRefresh: () -> Unit = {},
    val onLoadMore: () -> Unit = {},
    val onOpenSettings: () -> Unit = {},
    val onGoToRecord: () -> Unit = {},
    val onNav: (BottomNav.Tab) -> Unit = {},
)

/**
 * Session history. Folded: the list, and a row opens the detail screen. Unfolded: the list on the
 * left and the selected session's detail on the right, so reading one never leaves the list.
 */
@Composable
fun SessionsScreen(ui: SessionsUi, actions: SessionsActions, detailPane: @Composable () -> Unit, modifier: Modifier = Modifier) {
    val wide = booleanResource(R.bool.is_wide)
    val sp = InstUi.space
    Column(modifier.fillMaxSize().background(Inst.colors.bg)) {
        InstAppBar(stringResource(R.string.history_title), channel = ui.channel)
        if (wide) {
            Row(Modifier.weight(1f).fillMaxWidth()) {
                MainNavRail(null, actions.onNav)
                Column(Modifier.width(sp.listPane).fillMaxHeight()) { HistoryList(ui, actions, Modifier.weight(1f)) }
                Hairline(Modifier.width(sp.hairline).fillMaxHeight())
                Box(Modifier.weight(1f).fillMaxHeight()) { detailPane() }
            }
        } else {
            HistoryList(ui, actions, Modifier.weight(1f))
            MainNavBar(null, actions.onNav)
        }
    }
}

@Composable
private fun HistoryList(ui: SessionsUi, actions: SessionsActions, modifier: Modifier) {
    val sp = InstUi.space
    Column(modifier.fillMaxWidth()) {
        Row(
            Modifier.fillMaxWidth().padding(horizontal = sp.s16, vertical = sp.s8),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(sp.s8),
        ) {
            InstText(ui.subtitle, InstUi.type.caption, Modifier.weight(1f), maxLines = 2)
            InstButton(stringResource(R.string.ui_refresh), actions.onRefresh)
        }
        when (val list = ui.list) {
            RecentUi.Loading -> Column(Modifier.padding(horizontal = sp.s16), verticalArrangement = Arrangement.spacedBy(sp.s4)) {
                repeat(6) { SkeletonBlock(Modifier.fillMaxWidth().height(sp.row + sp.s16)) }
            }
            RecentUi.SignedOut -> Box(Modifier.padding(sp.s16)) {
                StatePanel(
                    stringResource(R.string.ui_history_signin_title), stringResource(R.string.history_not_signed_in),
                    tone = StateTone.Warning, actionText = stringResource(R.string.ui_history_open_settings), primaryAction = true, onAction = actions.onOpenSettings,
                )
            }
            RecentUi.Expired -> Box(Modifier.padding(sp.s16)) {
                StatePanel(
                    stringResource(R.string.ui_history_expired_title), stringResource(R.string.ui_history_signin_line),
                    tone = StateTone.Warning, actionText = stringResource(R.string.ui_history_open_settings), primaryAction = true, onAction = actions.onOpenSettings,
                )
            }
            RecentUi.Empty -> Box(Modifier.padding(sp.s16)) {
                StatePanel(
                    stringResource(R.string.ui_history_empty_title), stringResource(R.string.ui_history_empty_line),
                    actionText = stringResource(R.string.ui_history_empty_action), onAction = actions.onGoToRecord,
                )
            }
            is RecentUi.Error -> Box(Modifier.padding(sp.s16)) {
                StatePanel(
                    stringResource(R.string.ui_history_error_title), list.reason,
                    tone = StateTone.Error, actionText = stringResource(R.string.history_retry), onAction = actions.onRefresh,
                )
            }
            is RecentUi.Items -> {
                val state = rememberLazyListState()
                LaunchedEffect(state, list.rows.size) {
                    snapshotFlow { state.layoutInfo.visibleItemsInfo.lastOrNull()?.index ?: 0 }
                        .distinctUntilChanged()
                        .collect { if (it >= list.rows.size - PREFETCH) actions.onLoadMore() }
                }
                LazyColumn(
                    Modifier.fillMaxSize().refreshing(list.refreshing),
                    state = state,
                    contentPadding = PaddingValues(horizontal = sp.s16, vertical = sp.s4),
                    verticalArrangement = Arrangement.spacedBy(sp.s4),
                ) {
                    items(list.rows, key = { it.id }) { row ->
                        SessionRow(row, onClick = { actions.onOpen(row) }, selected = row.id == ui.selectedId)
                    }
                    if (ui.footer != ListFooter.NONE) item("footer") { LoadMoreFooter(ui.footer, actions.onLoadMore) }
                }
            }
        }
    }
}

@Composable
private fun LoadMoreFooter(footer: ListFooter, onClick: () -> Unit) {
    val sp = InstUi.space
    Box(
        Modifier.fillMaxWidth().heightIn(min = sp.touchTarget).clickable(enabled = footer != ListFooter.LOADING, role = Role.Button, onClick = onClick),
        contentAlignment = Alignment.Center,
    ) {
        InstText(
            stringResource(
                when (footer) {
                    ListFooter.ERROR -> R.string.history_load_more_failed
                    ListFooter.LOADING -> R.string.history_loading_more
                    else -> R.string.history_load_more
                }
            ),
            InstUi.type.caption, color = if (footer == ListFooter.ERROR) Inst.colors.error else Inst.colors.text2,
        )
    }
}

/** Shown in the right pane until a session is picked. */
@Composable
fun SelectSessionPane(modifier: Modifier = Modifier) {
    Box(modifier.fillMaxSize().padding(InstUi.space.s24), contentAlignment = Alignment.Center) {
        StatePanel(stringResource(R.string.ui_select_session), stringResource(R.string.ui_select_session_line))
    }
}

private const val PREFETCH = 4
