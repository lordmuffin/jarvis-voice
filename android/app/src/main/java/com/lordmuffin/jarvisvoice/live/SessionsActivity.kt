package com.lordmuffin.jarvisvoice.live

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.ProgressBar
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.lordmuffin.jarvisvoice.R
import com.lordmuffin.jarvisvoice.SettingsActivity
import com.lordmuffin.jarvisvoice.ui.setTextWithChannel
import java.text.DateFormat
import java.util.Date
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch

/** The signed-in user's Jarvis Live sessions on the server, newest first, paged. */
class SessionsActivity : AppCompatActivity() {

    private lateinit var settings: LiveSettings
    private lateinit var rvSessions: RecyclerView
    private lateinit var pbSessions: ProgressBar
    private lateinit var groupMessage: View
    private lateinit var tvMessage: TextView
    private lateinit var btnRetry: Button

    private val adapter = SessionsAdapter(onOpen = ::open, onFooter = { loadMore() })
    private var loadJob: Job? = null
    /** One client (and OkHttp connection pool) per sign-in; rebuilt when Settings changes the token or server. */
    private var client: LiveApiClient? = null
    private var clientKey: Pair<String, String?>? = null
    private var nextOffset = 0
    private var hasMore = false
    private var channelJob: Job? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_sessions)
        settings = LiveSettings(this)

        rvSessions = findViewById(R.id.rv_sessions)
        pbSessions = findViewById(R.id.pb_sessions)
        groupMessage = findViewById(R.id.group_sessions_message)
        tvMessage = findViewById(R.id.tv_sessions_message)
        btnRetry = findViewById(R.id.btn_sessions_retry)

        val layoutManager = LinearLayoutManager(this)
        rvSessions.layoutManager = layoutManager
        rvSessions.adapter = adapter
        rvSessions.addOnScrollListener(object : RecyclerView.OnScrollListener() {
            override fun onScrolled(recyclerView: RecyclerView, dx: Int, dy: Int) {
                if (dy <= 0) return
                if (layoutManager.findLastVisibleItemPosition() >= adapter.itemCount - PREFETCH) loadMore()
            }
        })

        findViewById<Button>(R.id.btn_sessions_refresh).setOnClickListener { reload() }
    }

    override fun onResume() {
        super.onResume()
        refreshSubtitle()
        // Also picks up a sign-in finished in Settings after this screen showed "Sign in".
        if (adapter.sessionCount == 0 && loadJob?.isActive != true) reload()
    }

    /** "Your Jarvis Live sessions on <host>", then the server's channel tag once it answers. */
    private fun refreshSubtitle() {
        val tv = findViewById<TextView>(R.id.tv_sessions_subtitle)
        val text = getString(R.string.history_subtitle, settings.serverUrl.removePrefix("https://").removePrefix("http://"))
        tv.text = text
        channelJob?.cancel()
        val client = runCatching { client() }.getOrNull() ?: return
        channelJob = lifecycleScope.launch {
            tv.setTextWithChannel(text, client.channel())
        }
    }

    private fun reload() {
        loadJob?.cancel()
        if (!settings.isConfigured) {
            adapter.replace(emptyList(), Footer.NONE)
            showMessage(getString(R.string.history_not_signed_in), MessageAction.SIGN_IN)
            return
        }
        groupMessage.visibility = View.GONE
        pbSessions.visibility = if (adapter.sessionCount == 0) View.VISIBLE else View.GONE
        loadJob = lifecycleScope.launch {
            try {
                val page = client().listSessions(PAGE_SIZE, 0)
                nextOffset = page.nextOffset
                hasMore = page.hasMore
                adapter.replace(page.items, if (hasMore) Footer.MORE else Footer.NONE)
                rvSessions.scrollToPosition(0)
                if (page.items.isEmpty()) showMessage(getString(R.string.history_empty), MessageAction.NONE)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                adapter.replace(emptyList(), Footer.NONE)
                showMessage(errorText(e), if (e is LiveHttpException && e.status == 401) MessageAction.SIGN_IN else MessageAction.RETRY)
            } finally {
                pbSessions.visibility = View.GONE
            }
        }
    }

    private fun loadMore() {
        if (!hasMore || loadJob?.isActive == true) return
        adapter.setFooter(Footer.LOADING)
        val offset = nextOffset
        loadJob = lifecycleScope.launch {
            try {
                val page = client().listSessions(PAGE_SIZE, offset)
                nextOffset = page.nextOffset
                hasMore = page.hasMore
                adapter.append(page.items, if (hasMore) Footer.MORE else Footer.NONE)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                adapter.setFooter(Footer.ERROR)
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

    private fun showMessage(text: String, action: MessageAction) {
        tvMessage.text = text
        btnRetry.visibility = if (action == MessageAction.NONE) View.GONE else View.VISIBLE
        btnRetry.setText(if (action == MessageAction.SIGN_IN) R.string.history_sign_in else R.string.history_retry)
        btnRetry.setOnClickListener {
            if (action == MessageAction.SIGN_IN) startActivity(Intent(this, SettingsActivity::class.java))
            else reload()
        }
        groupMessage.visibility = View.VISIBLE
    }

    private fun errorText(e: Exception): String = when {
        e is LiveHttpException && e.status == 401 -> getString(R.string.history_unauthorized)
        e is LiveHttpException -> getString(R.string.history_error, "HTTP ${e.status}")
        else -> getString(R.string.history_error, e.message ?: e.javaClass.simpleName)
    }

    private fun open(session: RemoteSession) {
        startActivity(SessionDetailActivity.intent(this, session.id, session.title))
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
                    SessionLabels.bindStatus(holder.status, s.status)
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

    fun bindStatus(view: TextView, status: String) {
        view.text = statusLabel(view.context, status)
        view.setTextColor(view.context.getColor(statusColor(status)))
    }

    fun modeLabel(ctx: Context, mode: String): String =
        ctx.getString(if (mode == "meeting") R.string.history_mode_meeting else R.string.history_mode_solo)

    /** "Oct 11, 2026, 9:14 AM · 42:10 · Meeting" */
    fun meta(ctx: Context, s: RemoteSession): String = listOfNotNull(
        s.startedAt?.let { DateFormat.getDateTimeInstance(DateFormat.MEDIUM, DateFormat.SHORT).format(Date(it.toEpochMilli())) },
        s.durationMs?.let { RemoteSessions.formatDuration(it) }
            ?: if (s.isInProgress) ctx.getString(R.string.history_in_progress) else null,
        modeLabel(ctx, s.mode),
    ).joinToString(" · ")
}
