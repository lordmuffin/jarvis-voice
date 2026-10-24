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
                                onPlay = model::onPlayTapped, onSeek = model::seekTo,
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

    enum class Footer { NONE, MORE, LOADING, ERROR }

    private enum class MessageAction { NONE, RETRY, SIGN_IN }

    private class SessionsAdapter(
        private val onOpen: (RemoteSession) -> Unit,
        private val onFooter: () -> Unit,
    ) : RecyclerView.Adapter<RecyclerView.ViewHolder>() {

        private val sessions = mutableListOf<RemoteSession>()
        private var footer = Footer.NONE

        val sessionCount: Int get() = sessions.size

        class Row(view: View) : RecyclerView.ViewHolder(view) {
            val title: TextView = view.findViewById(R.id.tv_rs_title)
            val status: TextView = view.findViewById(R.id.tv_rs_status)
            val meta: TextView = view.findViewById(R.id.tv_rs_meta)
        }

        class FooterHolder(view: View) : RecyclerView.ViewHolder(view) {
            val text: TextView = view.findViewById(R.id.tv_sessions_footer)
        }

        @Suppress("NotifyDataSetChanged")
        fun replace(items: List<RemoteSession>, footer: Footer) {
            sessions.clear()
            sessions.addAll(items.distinctBy { it.id })
            this.footer = footer
            notifyDataSetChanged()
        }

        fun append(items: List<RemoteSession>, footer: Footer) {
            // Sessions started since the first page shift the offsets; skip the repeats.
            val known = sessions.mapTo(HashSet()) { it.id }
            val fresh = items.filter { known.add(it.id) }
            val hadFooter = this.footer != Footer.NONE
            val start = sessions.size
            sessions.addAll(fresh)
            if (fresh.isNotEmpty()) notifyItemRangeInserted(start, fresh.size)
            this.footer = footer
            val footerPos = sessions.size
            when {
                hadFooter && footer == Footer.NONE -> notifyItemRemoved(footerPos)
                hadFooter -> notifyItemChanged(footerPos)
                footer != Footer.NONE -> notifyItemInserted(footerPos)
            }
        }

        fun setFooter(footer: Footer) {
            if (footer == this.footer) return
            val had = this.footer != Footer.NONE
            this.footer = footer
            when {
                had && footer == Footer.NONE -> notifyItemRemoved(sessions.size)
                had -> notifyItemChanged(sessions.size)
                else -> notifyItemInserted(sessions.size)
            }
        }

        override fun getItemCount() = sessions.size + if (footer == Footer.NONE) 0 else 1

        override fun getItemViewType(position: Int) = if (position < sessions.size) TYPE_ROW else TYPE_FOOTER

        override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): RecyclerView.ViewHolder {
            val inflater = LayoutInflater.from(parent.context)
            return if (viewType == TYPE_ROW) Row(inflater.inflate(R.layout.item_remote_session, parent, false))
            else FooterHolder(inflater.inflate(R.layout.item_sessions_footer, parent, false))
        }

        override fun onBindViewHolder(holder: RecyclerView.ViewHolder, position: Int) {
            val ctx = holder.itemView.context
            when (holder) {
                is Row -> {
                    val s = sessions[position]
                    holder.title.text = s.title ?: ctx.getString(R.string.history_untitled)
                    SessionLabels.bindStatus(holder.status, s.status, s.streaming)
                    holder.meta.text = SessionLabels.meta(ctx, s)
                    holder.itemView.setOnClickListener { onOpen(s) }
                }
                is FooterHolder -> {
                    holder.text.text = ctx.getString(
                        when (footer) {
                            Footer.ERROR -> R.string.history_load_more_failed
                            Footer.LOADING -> R.string.history_loading_more
                            else -> R.string.history_load_more
                        }
                    )
                    holder.itemView.isEnabled = footer != Footer.LOADING
                    holder.itemView.setOnClickListener { onFooter() }
                }
            }
        }

        companion object {
            const val TYPE_ROW = 0
            const val TYPE_FOOTER = 1
        }
    }

    private companion object {
        const val PAGE_SIZE = 20
        const val PREFETCH = 4
    }
}

/** Labels shared by the history list and the detail screen. */
internal object SessionLabels {

    fun statusLabel(ctx: Context, status: String): String = when (status) {
        RemoteSession.STATUS_LIVE -> ctx.getString(R.string.history_status_live)
        RemoteSession.STATUS_FINALIZING -> ctx.getString(R.string.history_status_finalizing)
        RemoteSession.STATUS_DONE -> ctx.getString(R.string.history_status_done)
        RemoteSession.STATUS_FAILED -> ctx.getString(R.string.history_status_failed)
        else -> status
    }

    /** State colors carry state only, and always come with the word. */
    fun statusColor(status: String): Int = when (status) {
        RemoteSession.STATUS_LIVE -> R.color.inst_accent
        RemoteSession.STATUS_FINALIZING -> R.color.inst_warning
        RemoteSession.STATUS_DONE -> R.color.inst_success
        RemoteSession.STATUS_FAILED -> R.color.inst_error
        else -> R.color.inst_text_2
    }

    /** A live session that is not receiving audio reads "Offline" (as on web), not "Live". */
    fun bindStatus(view: TextView, status: String, streaming: Boolean = true) {
        if (ConnectionChip.isOffline(status, streaming)) {
            view.bindOffline()
            return
        }
        view.contentDescription = null
        view.text = statusLabel(view.context, status)
        view.setTextColor(view.context.getColor(statusColor(status)))
    }

    fun modeLabel(ctx: Context, mode: String): String =
        ctx.getString(if (mode == "meeting") R.string.history_mode_meeting else R.string.history_mode_solo)

    /** "Oct 11, 2026, 9:14 AM · 42:10 · Meeting", plus "· Watch" for a recording made on the watch. */
    fun meta(ctx: Context, s: RemoteSession): String = listOfNotNull(
        s.startedAt?.let { DateFormat.getDateTimeInstance(DateFormat.MEDIUM, DateFormat.SHORT).format(Date(it.toEpochMilli())) },
        s.durationMs?.let { RemoteSessions.formatDuration(it) }
            ?: if (s.isInProgress) ctx.getString(R.string.history_in_progress) else null,
        modeLabel(ctx, s.mode),
        if (s.isFromWatch) ctx.getString(R.string.history_source_watch) else null,
    ).joinToString(" · ")
}
