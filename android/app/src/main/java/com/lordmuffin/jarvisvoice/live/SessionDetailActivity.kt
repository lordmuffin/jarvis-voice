package com.lordmuffin.jarvisvoice.live

import android.content.Context
import android.content.Intent
import android.os.Bundle
import androidx.activity.compose.setContent
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.lordmuffin.jarvisvoice.BottomNav
import com.lordmuffin.jarvisvoice.ui.compose.DetailActions
import com.lordmuffin.jarvisvoice.ui.compose.JarvisTheme
import com.lordmuffin.jarvisvoice.ui.compose.SessionDetailScreen

/**
 * One server session on its own screen (folded display): summary note, transcript, copilot actions
 * and its recorded audio. Unfolded, History shows the same content in a pane. Loading and playback
 * live in [SessionDetailModel]; the screen is Compose (`SessionDetailScreen`).
 */
class SessionDetailActivity : AppCompatActivity() {

    private lateinit var model: SessionDetailModel

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val settings = LiveSettings(this)
        val sessionId = intent.getStringExtra(EXTRA_ID) ?: run { finish(); return }
        val tab = savedInstanceState?.getString(STATE_TAB)?.let { runCatching { DetailTab.valueOf(it) }.getOrNull() } ?: DetailTab.SUMMARY
        model = SessionDetailModel(
            this, lifecycleScope, { settings.apiClient() }, sessionId,
            intent.getStringExtra(EXTRA_TITLE), tab, lifecycle,
        )
        setContent {
            JarvisTheme {
                SessionDetailScreen(
                    model.ui,
                    DetailActions(
                        onTab = model::selectTab, onRetry = model::load, onRefresh = model::load,
                        onPlay = model::onPlayTapped, onSeek = model::seekTo, onProcess = model::process,
                    ),
                    onBack = { finish() },
                    onNav = { BottomNav.open(this, it) },
                )
            }
        }
        model.load()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        outState.putString(STATE_TAB, model.ui.tab.name)
    }

    companion object {
        const val EXTRA_ID = "com.lordmuffin.jarvisvoice.live.SESSION_ID"
        const val EXTRA_TITLE = "com.lordmuffin.jarvisvoice.live.SESSION_TITLE"
        private const val STATE_TAB = "tab"

        fun intent(context: Context, sessionId: String, title: String?): Intent =
            Intent(context, SessionDetailActivity::class.java)
                .putExtra(EXTRA_ID, sessionId)
                .putExtra(EXTRA_TITLE, title)
    }
}
