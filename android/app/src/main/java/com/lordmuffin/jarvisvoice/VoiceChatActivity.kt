package com.lordmuffin.jarvisvoice

import android.Manifest
import android.content.pm.PackageManager
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.View
import android.widget.Button
import android.widget.FrameLayout
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.lifecycle.ViewModelProvider
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import android.view.LayoutInflater
import android.view.ViewGroup
import com.lordmuffin.jarvisvoice.chat.ChatStatus
import com.lordmuffin.jarvisvoice.chat.ConversationMessage
import com.lordmuffin.jarvisvoice.chat.VoiceChatViewModel
import com.lordmuffin.jarvisvoice.speech.SpeechEngine
import com.lordmuffin.jarvisvoice.speech.SpeechEngineFactory
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch

class VoiceChatActivity : AppCompatActivity() {

    private lateinit var viewModel: VoiceChatViewModel
    private lateinit var rvMessages: RecyclerView
    private lateinit var tvStreaming: TextView
    private lateinit var tvStatus: TextView
    private lateinit var btnTalk: Button

    private val adapter = MessageAdapter()
    private val uiScope = CoroutineScope(Dispatchers.Main + SupervisorJob())
    private val mainHandler = Handler(Looper.getMainLooper())

    private var engine: SpeechEngine? = null

    // true while the user is in a continuous back-and-forth conversation.
    // Mic auto-restarts after each assistant turn until the user cancels.
    private var conversationActive = false
    private var prevStatus = ChatStatus.IDLE

    // ── Lifecycle ─────────────────────────────────────────────────────────────

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_voice_chat)
        supportActionBar?.title = "Kai Chat"
        BottomNav.wire(this, BottomNav.Tab.CHAT)

        rvMessages  = findViewById(R.id.rv_messages)
        tvStreaming  = findViewById(R.id.tv_streaming)
        tvStatus    = findViewById(R.id.tv_chat_status)
        btnTalk     = findViewById(R.id.btn_talk)

        rvMessages.layoutManager = LinearLayoutManager(this).apply { stackFromEnd = true }
        rvMessages.adapter = adapter

        viewModel = ViewModelProvider(this)[VoiceChatViewModel::class.java]

        observeViewModel()
        btnTalk.setOnClickListener { onTalkTapped() }
    }

    override fun onPause() {
        super.onPause()
        // Stop the mic immediately — mic must not stay open when off-screen.
        // Do NOT cancel TTS/LLM here: screen auto-dim triggers onPause and
        // would cut off audio mid-sentence. TTS cancellation happens in onStop.
        conversationActive = false
        if (viewModel.status.value == ChatStatus.LISTENING) {
            engine?.stopListening()
        }
    }

    override fun onStop() {
        super.onStop()
        // Activity is now fully hidden — cancel in-flight LLM/TTS jobs and
        // destroy the SpeechEngine to free Whisper model weights (~300MB).
        conversationActive = false
        if (viewModel.status.value == ChatStatus.LISTENING) {
            engine?.stopListening()
        }
        viewModel.cancelActive()
        engine?.destroy()
        engine = null
    }

    override fun onDestroy() {
        super.onDestroy()
        conversationActive = false
        uiScope.cancel()
        engine?.destroy()
        engine = null
    }

    // ── Observe ───────────────────────────────────────────────────────────────

    private fun observeViewModel() {
        uiScope.launch {
            viewModel.messages.collect { msgs ->
                adapter.update(msgs)
                if (msgs.isNotEmpty()) rvMessages.scrollToPosition(msgs.size - 1)
            }
        }
        uiScope.launch {
            viewModel.streamingText.collect { text ->
                if (text.isEmpty()) {
                    tvStreaming.visibility = View.GONE
                } else {
                    tvStreaming.text = text
                    tvStreaming.visibility = View.VISIBLE
                }
            }
        }
        uiScope.launch {
            viewModel.status.collect { status ->
                updateTalkButton(status)
                tvStatus.text = when (status) {
                    ChatStatus.IDLE      -> ""
                    ChatStatus.LISTENING -> "Listening…"
                    ChatStatus.THINKING  -> "Thinking…"
                    ChatStatus.SPEAKING  -> "Speaking…"
                }

                // Auto-restart mic after assistant finishes speaking.
                // 600ms delay: lets the speaker ring down before the mic opens,
                // preventing the STT from capturing TTS echo.
                if (prevStatus == ChatStatus.SPEAKING && status == ChatStatus.IDLE && conversationActive) {
                    mainHandler.postDelayed({ startListening() }, 600)
                }
                prevStatus = status
            }
        }
    }

    private fun updateTalkButton(status: ChatStatus) {
        when (status) {
            ChatStatus.IDLE -> {
                btnTalk.text = "🎙  Start Talking"
                btnTalk.alpha = 1.0f
                btnTalk.isEnabled = true
            }
            ChatStatus.LISTENING -> {
                btnTalk.text = "⏹  Tap to Cancel"
                btnTalk.alpha = 1.0f
                btnTalk.isEnabled = true
            }
            ChatStatus.THINKING -> {
                btnTalk.text = "⏳  Processing…  (tap to stop)"
                btnTalk.alpha = 0.75f
                btnTalk.isEnabled = true
            }
            ChatStatus.SPEAKING -> {
                btnTalk.text = "🔊  Speaking…  (tap to stop)"
                btnTalk.alpha = 0.85f
                btnTalk.isEnabled = true
            }
        }
    }

    // ── Tap handler ───────────────────────────────────────────────────────────

    private fun onTalkTapped() {
        when (viewModel.status.value) {
            ChatStatus.IDLE -> {
                if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
                        == PackageManager.PERMISSION_GRANTED) {
                    startListening()
                } else {
                    requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), REQ_MIC)
                }
            }
            ChatStatus.LISTENING -> {
                // User cancelled before saying anything — stop mic, exit conversation mode
                conversationActive = false
                viewModel.setStatus(ChatStatus.IDLE)
                engine?.stopListening()
            }
            ChatStatus.THINKING, ChatStatus.SPEAKING -> {
                // Interrupt and exit conversation mode
                conversationActive = false
                viewModel.cancelActive()
            }
        }
    }

    private fun startListening() {
        conversationActive = true
        if (engine == null) engine = SpeechEngineFactory.create(this)
        viewModel.setStatus(ChatStatus.LISTENING)

        // holdMode = false: silence detection is ENABLED. The STT engine
        // automatically fires onFinal when the user stops speaking (SherpaOnnx:
        // 1.5s VAD; AndroidSTT: 10s timeout). No "Tap to Send" needed — the
        // conversation loop runs hands-free.
        engine?.startListening(
            holdMode  = false,
            onPartial = { },
            onFinal   = { text ->
                if (text.isNotBlank()) {
                    viewModel.sendText(text)
                } else {
                    // Silence or low-confidence result — restart mic immediately
                    // if still in conversation mode rather than hanging at IDLE.
                    viewModel.setStatus(ChatStatus.IDLE)
                    if (conversationActive) {
                        mainHandler.postDelayed({ startListening() }, 300)
                    }
                }
            },
            onError   = {
                viewModel.setStatus(ChatStatus.IDLE)
                if (conversationActive) {
                    mainHandler.postDelayed({ startListening() }, 500)
                }
            }
        )
    }

    // ── Permission ────────────────────────────────────────────────────────────

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<out String>,
        grantResults: IntArray
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == REQ_MIC && grantResults.firstOrNull() == PackageManager.PERMISSION_GRANTED)
            startListening()
    }

    // ── RecyclerView adapter ──────────────────────────────────────────────────

    private inner class MessageAdapter : RecyclerView.Adapter<MessageViewHolder>() {
        private var items: List<ConversationMessage> = emptyList()

        fun update(newItems: List<ConversationMessage>) {
            items = newItems
            notifyDataSetChanged()
        }

        override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): MessageViewHolder {
            val view = LayoutInflater.from(parent.context)
                .inflate(R.layout.item_chat_message, parent, false)
            return MessageViewHolder(view)
        }

        override fun onBindViewHolder(holder: MessageViewHolder, position: Int) =
            holder.bind(items[position])

        override fun getItemCount() = items.size
    }

    private inner class MessageViewHolder(view: View) : RecyclerView.ViewHolder(view) {
        val tvMsg: TextView = view.findViewById(R.id.tv_message)

        fun bind(msg: ConversationMessage) {
            tvMsg.text = msg.content
            val lp = tvMsg.layoutParams as FrameLayout.LayoutParams
            if (msg.role == "user") {
                lp.gravity = Gravity.END
                tvMsg.setBackgroundResource(R.drawable.chat_bubble_user)
                tvMsg.setTextColor(getColor(R.color.jv_bg))
            } else {
                lp.gravity = Gravity.START
                tvMsg.setBackgroundResource(R.drawable.chat_bubble_ai)
                tvMsg.setTextColor(getColor(R.color.jv_text))
            }
            tvMsg.layoutParams = lp
        }
    }

    companion object {
        private const val REQ_MIC = 55
    }
}
