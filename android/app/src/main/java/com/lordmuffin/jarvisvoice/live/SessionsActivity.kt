package com.lordmuffin.jarvisvoice.live

import android.content.Context
import android.content.Intent
import android.os.Bundle
import androidx.activity.compose.setContent
import androidx.appcompat.app.AppCompatActivity
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.lifecycle.lifecycleScope
import com.lordmuffin.jarvisvoice.BottomNav
import com.lordmuffin.jarvisvoice.R
import com.lordmuffin.jarvisvoice.SettingsActivity
import com.lordmuffin.jarvisvoice.ui.compose.DetailActions
import com.lordmuffin.jarvisvoice.ui.compose.JarvisTheme
import com.lordmuffin.jarvisvoice.ui.compose.ListFooter
import com.lordmuffin.jarvisvoice.ui.compose.RecentRow
import com.lordmuffin.jarvisvoice.ui.compose.RecentUi
import com.lordmuffin.jarvisvoice.ui.compose.SelectSessionPane
import com.lordmuffin.jarvisvoice.ui.compose.SessionDetailContent
import com.lordmuffin.jarvisvoice.ui.compose.SessionsActions
import com.lordmuffin.jarvisvoice.ui.compose.SessionsScreen
import com.lordmuffin.jarvisvoice.ui.compose.SessionsUi
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch

/**
 * The signed-in user's Jarvis Live sessions on the server, newest first, paged. Folded, a row opens
 * [SessionDetailActivity]; unfolded the detail shows in a pane beside the list. The screen is Compose
 * (`SessionsScreen`); this class owns loading and paging.
 */
class SessionsActivity : AppCompatActivity() {

    private lateinit var settings: LiveSettings
    private var ui by mutableStateOf(SessionsUi())

    private val sessions = mutableListOf<RemoteSession>()
    private var loadJob: Job? = null
    /** One client (and OkHttp connection pool) per sign-in; rebuilt when Settings changes the token or server. */
    private var client: LiveApiClient? = null
    private var clientKey: Pair<String, String?>? = null
    private var nextOffset = 0
    private var hasMore = false
    private var channelJob: Job? = null
    private val wide by lazy { resources.getBoolean(R.bool.is_wide) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        settings = LiveSettings(this)
        ui = ui.copy(selectedId = savedInstanceState?.getString(STATE_SELECTED) ?: intent.getStringExtra(EXTRA_SELECT))

        val actions = SessionsActions(
            onOpen = ::open,
            onRefresh = ::reload,
            onLoadMore = ::loadMore,
            onOpenSettings = { startActivity(Intent(this, SettingsActivity::class.java)) },
            onGoToRecord = { BottomNav.open(this, BottomNav.Tab.RECORD) },
            onNav = { BottomNav.open(this, it) },
        )
        setContent {
            JarvisTheme {
                SessionsScreen(ui, actions, detailPane = {
                    val id = ui.selectedId
                    if (id == null) {
                        SelectSessionPane()
                    } else {
                        val model = remember(id) {
                            SessionDetailModel(this@SessionsActivity, lifecycleScope, { settings.apiClient() }, id, titleOf(id), lifecycle = lifecycle)
                        }
                        DisposableEffect(model) {
                            model.load()
                            onDispose { model.release() }
                        }
                        SessionDetailContent(
                            model.ui,
                            DetailActions(
                                onTab = model::selectTab, onRetry = model::load, onRefresh = model::load,
                                onPlay = model::onPlayTapped, onSeek = model::seekTo, onProcess = model::process,
                            ),
                        )
                    }
                })
            }
        }
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        outState.putString(STATE_SELECTED, ui.selectedId)
    }

    override fun onResume() {
        super.onResume()
        refreshSubtitle()
        // Also picks up a sign-in finished in Settings after this screen showed "Sign in".
        if (sessions.isEmpty() && loadJob?.isActive != true) reload()
    }

    private fun titleOf(id: String): String? = sessions.firstOrNull { it.id == id }?.title

    /** "Your Jarvis Live sessions on <host>", then the server's channel tag once it answers. */
    private fun refreshSubtitle() {
        ui = ui.copy(subtitle = getString(R.string.history_subtitle, settings.serverUrl.removePrefix("https://").removePrefix("http://")))
        channelJob?.cancel()
        val client = runCatching { client() }.getOrNull() ?: return
        channelJob = lifecycleScope.launch {
            ui = ui.copy(channel = client.channel())
        }
    }

    private fun publish(footer: ListFooter, refreshing: Boolean = false) {
        ui = ui.copy(
            list = if (sessions.isEmpty()) ui.list else RecentUi.Items(sessions.map { SessionLabels.row(this, it) }, refreshing),
            footer = footer,
        )
    }

    private fun reload() {
        loadJob?.cancel()
        if (!settings.isConfigured) {
            sessions.clear()
            ui = ui.copy(list = RecentUi.SignedOut, footer = ListFooter.NONE)
            return
        }
        if (sessions.isEmpty()) ui = ui.copy(list = RecentUi.Loading) else publish(ui.footer, refreshing = true)
        loadJob = lifecycleScope.launch {
            try {
                val page = client().listSessions(PAGE_SIZE, 0)
                nextOffset = page.nextOffset
                hasMore = page.hasMore
                sessions.clear()
                sessions.addAll(page.items.distinctBy { it.id })
                val footer = if (hasMore) ListFooter.MORE else ListFooter.NONE
                if (sessions.isEmpty()) ui = ui.copy(list = RecentUi.Empty, footer = footer) else publish(footer)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                sessions.clear()
                ui = ui.copy(
                    list = if (e is LiveHttpException && e.status == 401) RecentUi.Expired else RecentUi.Error(SessionLabels.errorText(this@SessionsActivity, e)),
                    footer = ListFooter.NONE,
                )
            }
        }
    }

    private fun loadMore() {
        if (!hasMore || loadJob?.isActive == true) return
        ui = ui.copy(footer = ListFooter.LOADING)
        val offset = nextOffset
        loadJob = lifecycleScope.launch {
            try {
                val page = client().listSessions(PAGE_SIZE, offset)
                nextOffset = page.nextOffset
                hasMore = page.hasMore
                // Sessions started since the first page shift the offsets; skip the repeats.
                val known = sessions.mapTo(HashSet()) { it.id }
                sessions.addAll(page.items.filter { known.add(it.id) })
                publish(if (hasMore) ListFooter.MORE else ListFooter.NONE)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                ui = ui.copy(footer = ListFooter.ERROR)
            }
        }
    }

    private fun client(): LiveApiClient {
        val key = settings.serverUrl to settings.deviceToken
        if (key != clientKey) {
            client = settings.apiClient()
            clientKey = key
        }
        return client!!
    }

    private fun open(row: RecentRow) {
        if (wide) ui = ui.copy(selectedId = row.id)
        else startActivity(SessionDetailActivity.intent(this, row.id, row.title))
    }

    companion object {
        private const val PAGE_SIZE = 20
        private const val EXTRA_SELECT = "com.lordmuffin.jarvisvoice.live.SELECT_SESSION"
        private const val STATE_SELECTED = "selected"

        /** The history list with [sessionId] already open in the detail pane (unfolded). */
        fun intent(context: Context, sessionId: String): Intent =
            Intent(context, SessionsActivity::class.java).putExtra(EXTRA_SELECT, sessionId)
    }
}
