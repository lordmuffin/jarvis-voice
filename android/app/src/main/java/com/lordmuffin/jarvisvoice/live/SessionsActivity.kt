package com.lordmuffin.jarvisvoice.live

import android.content.Context
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
    /** One client (and OkHttp connection pool) for this screen. */
    private val client: LiveApiClient by lazy { settings.apiClient() }
    private var nextOffset = 0
    private var hasMore = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_sessions)
        settings = LiveSettings(this)

        rvSessions = findViewById(R.id.rv_sessions)
        pbSessions = findViewById(R.id.pb_sessions)
        groupMessage = findViewById(R.id.group_sessions_message)
        tvMessage = findViewById(R.id.tv_sessions_message)
        btnRetry = findViewById(R.id.btn_sessions_retry)

        findViewById<TextView>(R.id.tv_sessions_subtitle).text =
            getString(R.string.history_subtitle, settings.serverUrl.removePrefix("https://").removePrefix("http://"))

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
        btnRetry.setOnClickListener { reload() }

        reload()
    }

    private fun reload() {
        loadJob?.cancel()
        if (!settings.isConfigured) {
            adapter.replace(emptyList(), Footer.NONE)
            showMessage(getString(R.string.history_not_signed_in), retry = false)
            return
        }
        groupMessage.visibility = View.GONE
        pbSessions.visibility = if (adapter.sessionCount == 0) View.VISIBLE else View.GONE
        loadJob = lifecycleScope.launch {
            try {
                val page = client.listSessions(PAGE_SIZE, 0)
                nextOffset = page.nextOffset
                hasMore = page.hasMore
                adapter.replace(page.items, if (hasMore) Footer.MORE else Footer.NONE)
                rvSessions.scrollToPosition(0)
                if (page.items.isEmpty()) showMessage(getString(R.string.history_empty), retry = false)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                adapter.replace(emptyList(), Footer.NONE)
                showMessage(errorText(e), retry = !(e is LiveHttpException && e.status == 401))
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
                val page = client.listSessions(PAGE_SIZE, offset)
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

    private fun showMessage(text: String, retry: Boolean) {
        tvMessage.text = text
        btnRetry.visibility = if (retry) View.VISIBLE else View.GONE
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
        RemoteSession.STATUS_LIVE -> R.color.jv_accent
        RemoteSession.STATUS_FINALIZING -> R.color.jv_warning
        RemoteSession.STATUS_DONE -> R.color.jv_success
        RemoteSession.STATUS_FAILED -> R.color.jv_error
        else -> R.color.jv_text2
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
