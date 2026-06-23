package com.lordmuffin.jarvisvoice

import android.Manifest
import android.content.pm.PackageManager
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.KeyEvent
import android.view.View
import android.view.ViewGroup
import android.view.inputmethod.EditorInfo
import android.view.inputmethod.InputMethodManager
import android.widget.Button
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.PopupMenu
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.lifecycle.ViewModelProvider
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import android.view.LayoutInflater
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

    // UI refs
    private lateinit var rvMessages:   RecyclerView
    private lateinit var tvStreaming:  TextView
    private lateinit var tvStatus:     TextView
    private lateinit var btnModeVoice: Button
    private lateinit var btnModeType:  Button
    private lateinit var btnModel:     Button
    private lateinit var btnTalk:      Button
    private lateinit var llTypeInput:  LinearLayout
    private lateinit var etMessage:    EditText
    private lateinit var btnSendText:  Button

    private val adapter = MessageAdapter()
    private val uiScope = CoroutineScope(Dispatchers.Main + SupervisorJob())
    private val mainHandler = Handler(Looper.getMainLooper())

    private var engine: SpeechEngine? = null

    private var voiceMode = true          // true = voice input, false = text input
    private var conversationActive = false
    private var prevStatus = ChatStatus.IDLE

    // ── Lifecycle ─────────────────────────────────────────────────────────────

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_voice_chat)
        supportActionBar?.title = "Kai Chat"
        BottomNav.wire(this, BottomNav.Tab.CHAT)

        rvMessages   = findViewById(R.id.rv_messages)
        tvStreaming   = findViewById(R.id.tv_streaming)
        tvStatus      = findViewById(R.id.tv_chat_status)
        btnModeVoice  = findViewById(R.id.btn_mode_voice)
        btnModeType   = findViewById(R.id.btn_mode_type)
        btnModel      = findViewById(R.id.btn_model)
        btnTalk       = findViewById(R.id.btn_talk)
        llTypeInput   = findViewById(R.id.ll_type_input)
        etMessage     = findViewById(R.id.et_message)
        btnSendText   = findViewById(R.id.btn_send_text)

        rvMessages.layoutManager = LinearLayoutManager(this).apply { stackFromEnd = true }
        rvMessages.adapter = adapter

        viewModel = ViewModelProvider(this)[VoiceChatViewModel::class.java]

        observeViewModel()

        btnModeVoice.setOnClickListener { setVoiceMode(true) }
        btnModeType.setOnClickListener  { setVoiceMode(false) }
        btnModel.setOnClickListener     { showModelMenu() }
        btnTalk.setOnClickListener      { onTalkTapped() }
        btnSendText.setOnClickListener  { sendTypedMessage() }

        // IME "send" action from soft keyboard also triggers send
        etMessage.setOnEditorActionListener { _, actionId, event ->
            if (actionId == EditorInfo.IME_ACTION_SEND ||
                (event?.keyCode == KeyEvent.KEYCODE_ENTER && event.action == KeyEvent.ACTION_DOWN)) {
                sendTypedMessage()
                true
            } else false
        }

        applyModeUi()
    }

    override fun onPause() {
        super.onPause()
        conversationActive = false
        if (viewModel.status.value == ChatStatus.LISTENING) engine?.stopListening()
    }

    override fun onStop() {
        super.onStop()
        conversationActive = false
        if (viewModel.status.value == ChatStatus.LISTENING) engine?.stopListening()
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

    // ── Mode toggle ───────────────────────────────────────────────────────────

    private fun setVoiceMode(voice: Boolean) {
        voiceMode = voice

        // Cancel any active voice turn when switching to type mode
        if (!voice && viewModel.status.value != ChatStatus.IDLE) {
            conversationActive = false
            viewModel.cancelActive()
        }

        applyModeUi()
    }

    private fun applyModeUi() {
        if (voiceMode) {
            btnModeVoice.setBackgroundResource(R.drawable.pill_background_accent)
            btnModeVoice.setTextColor(getColor(R.color.jv_text))
            btnModeType.setBackgroundResource(R.drawable.pill_background_dim)
            btnModeType.setTextColor(getColor(R.color.jv_text2))
            btnTalk.visibility    = View.VISIBLE
            llTypeInput.visibility = View.GONE
            dismissKeyboard()
        } else {
            btnModeType.setBackgroundResource(R.drawable.pill_background_accent)
            btnModeType.setTextColor(getColor(R.color.jv_text))
            btnModeVoice.setBackgroundResource(R.drawable.pill_background_dim)
            btnModeVoice.setTextColor(getColor(R.color.jv_text2))
            btnTalk.visibility    = View.GONE
            llTypeInput.visibility = View.VISIBLE
        }
    }

    private fun dismissKeyboard() {
        val imm = getSystemService(INPUT_METHOD_SERVICE) as InputMethodManager
        imm.hideSoftInputFromWindow(etMessage.windowToken, 0)
        etMessage.clearFocus()
    }

    // ── Model selector ────────────────────────────────────────────────────────

    private fun showModelMenu() {
        val models = viewModel.availableModels.value
        if (models.isEmpty()) return

        val popup = PopupMenu(this, btnModel)
        models.forEachIndexed { i, name ->
            val item = popup.menu.add(0, i, i, name)
            // Checkmark the currently selected model
            item.isChecked = (name == viewModel.selectedModel.value)
        }
        popup.setOnMenuItemClickListener { item ->
            val selected = models[item.itemId]
            viewModel.selectModel(selected)
            updateModelButton(selected)
            true
        }
        popup.show()
    }

    private fun updateModelButton(model: String) {
        // Truncate long model names to keep the button compact
        val label = if (model.length > 14) model.take(13) + "…" else model
        btnModel.text = "$label ▾"
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
                if (voiceMode) updateTalkButton(status)
                tvStatus.text = when (status) {
                    ChatStatus.IDLE      -> ""
                    ChatStatus.LISTENING -> "Listening…"
                    ChatStatus.THINKING  -> "Thinking…"
                    ChatStatus.SPEAKING  -> "Speaking…"
                }
                // Auto-restart mic after speaking. 600ms lets the speaker ring
                // down before the mic opens so TTS echo isn't captured.
                if (prevStatus == ChatStatus.SPEAKING && status == ChatStatus.IDLE
                        && conversationActive && voiceMode) {
                    mainHandler.postDelayed({ startListening() }, 600)
                }
                prevStatus = status
            }
        }
        uiScope.launch {
            viewModel.selectedModel.collect { model ->
                updateModelButton(model)
            }
        }
    }

    private fun updateTalkButton(status: ChatStatus) {
        when (status) {
            ChatStatus.IDLE -> {
                btnTalk.text  = "🎙  Start Talking"
                btnTalk.alpha = 1.0f
                btnTalk.isEnabled = true
            }
            ChatStatus.LISTENING -> {
                btnTalk.text  = "⏹  Tap to Cancel"
                btnTalk.alpha = 1.0f
                btnTalk.isEnabled = true
            }
            ChatStatus.THINKING -> {
                btnTalk.text  = "⏳  Processing…  (tap to stop)"
                btnTalk.alpha = 0.75f
                btnTalk.isEnabled = true
            }
            ChatStatus.SPEAKING -> {
                btnTalk.text  = "🔊  Speaking…  (tap to stop)"
                btnTalk.alpha = 0.85f
                btnTalk.isEnabled = true
            }
        }
    }

    // ── Voice input ───────────────────────────────────────────────────────────

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
                // User changed mind — cancel without sending
                conversationActive = false
                viewModel.setStatus(ChatStatus.IDLE)
                engine?.stopListening()
            }
            ChatStatus.THINKING, ChatStatus.SPEAKING -> {
                conversationActive = false
                viewModel.cancelActive()
            }
        }
    }

    private fun startListening() {
        conversationActive = true
        if (engine == null) engine = SpeechEngineFactory.create(this)
        viewModel.setStatus(ChatStatus.LISTENING)

        // holdMode=false: silence auto-fires onFinal (SherpaOnnx: 1.5s; AndroidSTT: 10s).
        // No "Tap to Send" needed — the conversation loop runs hands-free.
        engine?.startListening(
            holdMode  = false,
            onPartial = { },
            onFinal   = { text ->
                if (text.isNotBlank()) {
                    viewModel.sendText(text)
                } else {
                    viewModel.setStatus(ChatStatus.IDLE)
                    if (conversationActive) mainHandler.postDelayed({ startListening() }, 300)
                }
            },
            onError   = {
                viewModel.setStatus(ChatStatus.IDLE)
                if (conversationActive) mainHandler.postDelayed({ startListening() }, 500)
            }
        )
    }

    // ── Text input ────────────────────────────────────────────────────────────

    private fun sendTypedMessage() {
        val text = etMessage.text.toString().trim()
        if (text.isEmpty()) return
        etMessage.setText("")
        dismissKeyboard()
        viewModel.sendText(text)
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
