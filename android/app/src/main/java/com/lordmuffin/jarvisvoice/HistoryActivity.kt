package com.lordmuffin.jarvisvoice

import android.content.ClipData
import android.content.ClipboardManager
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

class HistoryActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_history)
        supportActionBar?.title = "Dictation History"
        BottomNav.wire(this, BottomNav.Tab.HISTORY)

        val db       = DictationHistoryManager(this)
        val sessions = db.getRecentSessions()
        val lifetime = db.getLifetimeStats()
        val last     = db.getLastSession()

        findViewById<TextView>(R.id.tv_last_words).text =
            last?.wordCount?.toString() ?: "—"
        findViewById<TextView>(R.id.tv_last_wpm).text =
            last?.let { "%.0f".format(it.wpm) } ?: "—"
        findViewById<TextView>(R.id.tv_total_words).text =
            lifetime.totalWords.toString()
        findViewById<TextView>(R.id.tv_avg_wpm).text =
            "%.0f".format(lifetime.avgWpm)

        val rv = findViewById<RecyclerView>(R.id.rv_sessions)
        rv.layoutManager = LinearLayoutManager(this)
        rv.adapter = SessionAdapter(sessions)
    }

    private fun copyToClipboard(text: String, label: String = "dictation") {
        val cm = getSystemService(CLIPBOARD_SERVICE) as ClipboardManager
        cm.setPrimaryClip(ClipData.newPlainText(label, text))
        Toast.makeText(this, "Copied", Toast.LENGTH_SHORT).show()
    }

    private inner class SessionAdapter(private val items: List<DictationSession>) :
        RecyclerView.Adapter<SessionAdapter.VH>() {

        private val sdf = SimpleDateFormat("MMM d, h:mm a", Locale.getDefault())

        // Tracks which session timestamps are expanded
        private val expandedIds = HashSet<Long>()

        inner class VH(v: View) : RecyclerView.ViewHolder(v) {
            val tvTime:             TextView = v.findViewById(R.id.tv_session_time)
            val tvStats:            TextView = v.findViewById(R.id.tv_session_stats)
            val tvChevron:          TextView = v.findViewById(R.id.tv_expand_chevron)
            val tvTranscript:       TextView = v.findViewById(R.id.tv_session_transcript)
            val tvRaw:              TextView = v.findViewById(R.id.tv_raw_transcript)
            val tvEnhancedLabel:    TextView = v.findViewById(R.id.tv_enhanced_label)
            val layoutDual:         View    = v.findViewById(R.id.layout_dual_pass)
            val layoutActions:      View    = v.findViewById(R.id.layout_expanded_actions)
            val btnCopyFinal:       Button  = v.findViewById(R.id.btn_copy_final)
            val btnCopyRaw:         Button  = v.findViewById(R.id.btn_copy_raw)
        }

        override fun onCreateViewHolder(parent: ViewGroup, viewType: Int) =
            VH(LayoutInflater.from(parent.context)
                .inflate(R.layout.item_session, parent, false))

        override fun getItemCount() = items.size

        override fun onBindViewHolder(holder: VH, position: Int) {
            val s = items[position]
            val isExpanded = expandedIds.contains(s.timestamp)
            val wasEnhanced = s.rawTranscript.isNotBlank() && s.rawTranscript != s.transcript

            holder.tvTime.text       = sdf.format(Date(s.timestamp))
            holder.tvStats.text      = "${s.wordCount} words · ${"%.0f".format(s.wpm)} wpm"
            holder.tvTranscript.text = s.transcript
            holder.tvChevron.text    = if (isExpanded) "▲" else "▼"

            // Dual-pass section
            if (wasEnhanced) {
                holder.layoutDual.visibility = View.VISIBLE
                holder.tvRaw.text = s.rawTranscript
                holder.tvEnhancedLabel.text = if (s.llmModel.isNotBlank()) s.llmModel else "Enhanced"
            } else {
                holder.layoutDual.visibility = View.GONE
            }

            // Expanded state: remove line clamp, show action buttons
            if (isExpanded) {
                holder.tvTranscript.maxLines = Int.MAX_VALUE
                holder.tvTranscript.ellipsize = null
                holder.tvRaw.maxLines = Int.MAX_VALUE
                holder.tvRaw.ellipsize = null
                holder.layoutActions.visibility = View.VISIBLE
                holder.btnCopyRaw.visibility = if (wasEnhanced) View.VISIBLE else View.GONE
            } else {
                holder.tvTranscript.maxLines = 2
                holder.tvTranscript.ellipsize = android.text.TextUtils.TruncateAt.END
                holder.tvRaw.maxLines = 2
                holder.tvRaw.ellipsize = android.text.TextUtils.TruncateAt.END
                holder.layoutActions.visibility = View.GONE
            }

            // Tap — toggle expand/collapse
            holder.itemView.setOnClickListener {
                if (expandedIds.contains(s.timestamp)) {
                    expandedIds.remove(s.timestamp)
                } else {
                    expandedIds.add(s.timestamp)
                }
                notifyItemChanged(position)
            }

            // Long-press — copy final transcript immediately
            holder.itemView.setOnLongClickListener {
                copyToClipboard(s.transcript)
                true
            }

            // Copy buttons (only visible when expanded)
            holder.btnCopyFinal.setOnClickListener {
                copyToClipboard(s.transcript)
            }
            holder.btnCopyRaw.setOnClickListener {
                copyToClipboard(s.rawTranscript, "whisper")
            }
        }
    }
}
