package com.lordmuffin.jarvisvoice.screenshots

import com.lordmuffin.jarvisvoice.live.DetailTab
import com.lordmuffin.jarvisvoice.live.DetailUi
import com.lordmuffin.jarvisvoice.ui.compose.DetailActions
import com.lordmuffin.jarvisvoice.ui.compose.JarvisTheme
import com.lordmuffin.jarvisvoice.ui.compose.RecentUi
import com.lordmuffin.jarvisvoice.ui.compose.SelectSessionPane
import com.lordmuffin.jarvisvoice.ui.compose.SessionDetailContent
import com.lordmuffin.jarvisvoice.ui.compose.SessionDetailScreen
import com.lordmuffin.jarvisvoice.ui.compose.SessionsActions
import com.lordmuffin.jarvisvoice.ui.compose.SessionsScreen
import com.lordmuffin.jarvisvoice.ui.compose.SessionsUi
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.junit.runners.Parameterized

/** History (list, and list + detail when unfolded), Session Detail, and their empty/error/loading states. */
@RunWith(Parameterized::class)
class HistoryScreenTest(private val variant: Variant) {

    @get:Rule val paparazzi = paparazziFor(variant)

    private fun history(name: String, ui: SessionsUi, detail: DetailUi? = null) =
        paparazzi.snapshot(name = "${name}_${variant.tag}") {
            JarvisTheme(darkTheme = variant.dark) {
                SessionsScreen(
                    ui, SessionsActions(),
                    detailPane = { if (detail == null) SelectSessionPane() else SessionDetailContent(detail, DetailActions()) },
                )
            }
        }

    private fun detail(name: String, ui: DetailUi) = paparazzi.snapshot(name = "${name}_${variant.tag}") {
        JarvisTheme(darkTheme = variant.dark) { SessionDetailScreen(ui, DetailActions(), onBack = {}, onNav = {}) }
    }

    @Test fun list() = history("history_list", Fixtures.sessions)
    @Test fun listWithSelection() = history("history_selected", Fixtures.sessions.copy(selectedId = "s2"), Fixtures.detail)
    @Test fun listRefreshing() = history("history_refreshing", Fixtures.sessions.copy(list = RecentUi.Items(Fixtures.recentRows, refreshing = true)))
    @Test fun empty() = history("history_empty", Fixtures.sessions.copy(list = RecentUi.Empty, footer = com.lordmuffin.jarvisvoice.ui.compose.ListFooter.NONE))
    @Test fun signedOut() = history("history_signed_out", Fixtures.sessions.copy(list = RecentUi.SignedOut))
    @Test fun expired() = history("history_expired", Fixtures.sessions.copy(list = RecentUi.Expired))
    @Test fun error() = history("history_error", Fixtures.sessions.copy(list = RecentUi.Error("Couldn't load sessions: HTTP 502")))
    @Test fun loading() = history("history_loading", Fixtures.sessions.copy(list = RecentUi.Loading))

    // Recently deleted: the list, its empty state, and a deleted session's page with Restore.
    @Test fun deletedList() = history("history_deleted", Fixtures.deletedSessions)
    @Test fun deletedEmpty() = history("history_deleted_empty", Fixtures.deletedSessions.copy(list = RecentUi.Empty, footer = com.lordmuffin.jarvisvoice.ui.compose.ListFooter.NONE))
    @Test fun detailDeleted() = detail("detail_deleted", Fixtures.detailDeleted)
    @Test fun detailConfirmDelete() = detail("detail_confirm_delete", Fixtures.detail.copy(confirmingDelete = true))
    @Test fun detailRecordingCannotBeDeleted() = detail("detail_in_progress_no_delete", Fixtures.detailFinalizing)

    @Test fun detailSummary() = detail("detail_summary", Fixtures.detail)
    @Test fun detailTranscript() = detail("detail_transcript", Fixtures.detail.copy(tab = DetailTab.TRANSCRIPT))
    @Test fun detailActions() = detail("detail_actions", Fixtures.detail.copy(tab = DetailTab.ACTIONS))
    @Test fun detailProcessing() = detail("detail_processing", Fixtures.detailProcessing)
    @Test fun detailFinalizing() = detail("detail_finalizing", Fixtures.detailFinalizing)
    @Test fun detailLoading() = detail("detail_loading", Fixtures.detailLoading)
    @Test fun detailError() = detail("detail_error", Fixtures.detailError)

    companion object {
        @JvmStatic @Parameterized.Parameters(name = "{0}")
        fun variants(): List<Variant> = Variant.ALL
    }
}
