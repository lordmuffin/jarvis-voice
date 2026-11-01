package com.lordmuffin.jarvisvoice.call

import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.lordmuffin.jarvisvoice.BottomNav
import com.lordmuffin.jarvisvoice.R
import kotlinx.coroutines.launch
import java.util.Locale

class CallActivity : AppCompatActivity() {

    private lateinit var viewModel: CallViewModel
    private lateinit var rvTranscript: RecyclerView
    private lateinit var llInput: LinearLayout
    private lateinit var btnCall: Button
    private lateinit var btnEnd: Button
    private lateinit var etPhone: EditText
    private lateinit var etContact: EditText
    private lateinit var etContext: EditText
    private lateinit var tvContact: TextView
    private lateinit var tvNumber: TextView
    private lateinit var tvStatus: TextView
    private lateinit var tvElapsed: TextView

    private val adapter = TranscriptAdapter()
    private val handler = Handler(Looper.getMainLooper())
    private var elapsedSeconds = 0
    private val timerRunnable = object : Runnable {
        override fun run() {
            elapsedSeconds++
            val m = elapsedSeconds / 60
            val s = elapsedSeconds % 60
            tvElapsed.text = String.format(Locale.US, "%d:%02d", m, s)
            handler.postDelayed(this, 1000)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_call)
        supportActionBar?.title = "Jarvis Calls"
        BottomNav.wire(this, BottomNav.Tab.CALLS)

        rvTranscript = findViewById(R.id.rv_transcript)
        llInput      = findViewById(R.id.ll_input)
        btnCall      = findViewById(R.id.btn_call)
        btnEnd       = findViewById(R.id.btn_end_call)
        etPhone      = findViewById(R.id.et_phone)
        etContact    = findViewById(R.id.et_contact)
        etContext    = findViewById(R.id.et_context)
        tvContact    = findViewById(R.id.tv_call_contact)
        tvNumber     = findViewById(R.id.tv_call_number)
        tvStatus     = findViewById(R.id.tv_call_status)
        tvElapsed    = findViewById(R.id.tv_call_elapsed)

        rvTranscript.layoutManager = LinearLayoutManager(this).apply { stackFromEnd = true }
        rvTranscript.adapter = adapter

        viewModel = ViewModelProvider(this)[CallViewModel::class.java]

        btnCall.setOnClickListener { onCallClicked() }
        btnEnd.setOnClickListener  { viewModel.endCall() }

        lifecycleScope.launch {
            viewModel.status.collect { status ->
                when (status) {
                    CallStatus.IDLE -> {
                        llInput.visibility = View.VISIBLE
                        btnEnd.visibility  = View.GONE
                        handler.removeCallbacks(timerRunnable)
                    }
                    CallStatus.CONNECTING -> {
                        llInput.visibility = View.GONE
                        btnEnd.visibility  = View.VISIBLE
                        tvContact.text = etContact.text.toString().ifBlank { etPhone.text.toString() }
                        tvNumber.text  = etPhone.text.toString()
                    }
                    CallStatus.ACTIVE -> {
                        elapsedSeconds = 0
                        handler.post(timerRunnable)
                    }
                    CallStatus.ENDED -> {
                        btnEnd.visibility = View.GONE
                        handler.removeCallbacks(timerRunnable)
                    }
                }
            }
        }

        lifecycleScope.launch {
            viewModel.statusText.collect { tvStatus.text = it }
        }

        lifecycleScope.launch {
            viewModel.transcript.collect { lines ->
                adapter.update(lines)
                if (lines.isNotEmpty()) rvTranscript.scrollToPosition(lines.size - 1)
            }
        }

        lifecycleScope.launch {
            viewModel.error.collect { err ->
                if (err != null) Toast.makeText(this@CallActivity, err, Toast.LENGTH_LONG).show()
            }
        }
    }

    private fun onCallClicked() {
        val phone = etPhone.text.toString().trim()
        if (phone.isEmpty()) {
            etPhone.error = "Enter a phone number"
            return
        }
        val contact = etContact.text.toString().trim()
        val ctx     = etContext.text.toString().trim()
        viewModel.initiateCall(phone, contact.ifBlank { phone }, ctx.ifBlank { "General check-in call." })
    }

    override fun onDestroy() {
        super.onDestroy()
        handler.removeCallbacks(timerRunnable)
    }
}

// ── Transcript adapter ────────────────────────────────────────────────────────

private class TranscriptAdapter : RecyclerView.Adapter<TranscriptAdapter.VH>() {

    private var items: List<TranscriptLine> = emptyList()

    fun update(newItems: List<TranscriptLine>) {
        items = newItems
        notifyDataSetChanged()
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int) =
        VH(LayoutInflater.from(parent.context).inflate(R.layout.item_chat_message, parent, false))

    override fun getItemCount() = items.size

    override fun onBindViewHolder(h: VH, pos: Int) {
        val line = items[pos]
        val isJarvis = line.role == "jarvis"
        h.tv.text = if (isJarvis) "🤖 ${line.text}" else "👤 ${line.text}"
        val lp = h.tv.layoutParams as android.widget.FrameLayout.LayoutParams
        if (isJarvis) {
            lp.gravity = Gravity.START
            h.tv.setBackgroundResource(R.drawable.chat_bubble_ai)
            h.tv.setTextColor(h.tv.context.getColor(R.color.jv_text))
        } else {
            lp.gravity = Gravity.END
            h.tv.setBackgroundResource(R.drawable.chat_bubble_user)
            h.tv.setTextColor(h.tv.context.getColor(R.color.jv_bg))
        }
        h.tv.layoutParams = lp
    }

    class VH(view: View) : RecyclerView.ViewHolder(view) {
        val tv: TextView = view.findViewById(R.id.tv_message)
    }
}
