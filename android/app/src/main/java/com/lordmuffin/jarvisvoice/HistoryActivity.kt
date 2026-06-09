package com.lordmuffin.jarvisvoice

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
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

    private inner class SessionAdapter(private val items: List<DictationSession>) :
        RecyclerView.Adapter<SessionAdapter.VH>() {

        private val sdf = SimpleDateFormat("MMM d, h:mm a", Locale.getDefault())

        inner class VH(v: View) : RecyclerView.ViewHolder(v) {
            val tvTime:       TextView = v.findViewById(R.id.tv_session_time)
            val tvStats:      TextView = v.findViewById(R.id.tv_session_stats)
            val tvTranscript: TextView = v.findViewById(R.id.tv_session_transcript)
        }

        override fun onCreateViewHolder(parent: ViewGroup, viewType: Int) =
            VH(LayoutInflater.from(parent.context)
                .inflate(R.layout.item_session, parent, false))

        override fun getItemCount() = items.size

        override fun onBindViewHolder(holder: VH, position: Int) {
            val s = items[position]
            holder.tvTime.text       = sdf.format(Date(s.timestamp))
            holder.tvStats.text      = "${s.wordCount} words · ${"%.0f".format(s.wpm)} wpm"
            holder.tvTranscript.text = s.transcript
        }
    }
}
