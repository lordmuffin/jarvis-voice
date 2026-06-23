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
    private var voiceMode = true

    // true while the conversation loop is running.
    @Volatile private var conversationActive = false

    // true while the shadow barge-in mic is running during SPEAKING.
    @Volatile private var bargeInListening = false

    // Set before stopListening() when the user explicitly stops the conversation
    // mid-utterance, so onFinal discards the partial text instead of sending it.
    @Volatile private var discardNextResult = false

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

        etMessage.setOnEditorActionListener { _, actionId, event ->
            if (actionId == EditorInfo.IME_ACTION_SEND ||
                (event?.keyCode == KeyEvent.KEYCODE_ENTER && event.action == KeyEvent.ACTION_DOWN)) {
                sendTypedMessage(); true
            } else false
        }

        applyModeUi()
    }

    override fun onPause() {
        super.onPause()
        if (conversationActive) stopConversation()
    }

    override fun onStop() {
        super.onStop()
        stopConversation()
        viewModel.cancelActive()
        engine?.destroy()
        engine = null
    }

    override fun onDestroy() {
        super.onDestroy()
        uiScope.cancel()
        engine?.destroy()
        engine = null
    }

    // ── Conversation control ──────────────────────────────────────────────────

    private fun onTalkTapped() {
        when {
            !conversationActive -> {
                if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
                        == PackageManager.PERMISSION_GRANTED) {
                    startConversation()
                } else {
                    requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), REQ_MIC)
                }
            }
            viewModel.status.value == ChatStatus.SPEAKING -> interruptAndListen()
            else -> {
                // Listening / Thinking / Tool call → end the conversation entirely
                stopConversation()
                viewModel.cancelActive()
            }
        }
    }

    // Manual barge-in (button tap while SPEAKING).
    private fun interruptAndListen() {
        mainHandler.removeCallbacksAndMessages(null)
        bargeInListening = false
        engine?.cancelListening()  // stop shadow mic
        prevStatus = ChatStatus.IDLE  // skip SPEAKING→IDLE observer restart
        viewModel.interruptSpeaking()
        startListening()
    }

    // Auto barge-in: called by the shadow mic when speech is detected while SPEAKING.
    private fun interruptWithText(text: String) {
        mainHandler.removeCallbacksAndMessages(null)
        bargeInListening = false
        prevStatus = ChatStatus.IDLE  // skip SPEAKING→IDLE observer restart
        viewModel.interruptSpeaking()  // cancel TTS + set IDLE
        viewModel.sendText(text)       // immediately process the detected speech
    }

    // Shadow mic — runs silently during SPEAKING so the user can barge in without tapping.
    // AEC on the AudioRecord session suppresses TTS bleed so Kai's voice doesn't self-trigger.
    private fun startBargeInListening() {
        if (!conversationActive || !voiceMode || !bargeInListening) return
        if (engine == null) engine = SpeechEngineFactory.create(this)
        // Do NOT change viewModel status — it stays SPEAKING.
        engine?.startListening(
            holdMode  = false,
            onPartial = { },
            onFinal   = { text ->
                if (!bargeInListening) return@startListening
                if (text.isNotBlank() && !isSherpaArtifact(text)) {
                    mainHandler.post { interruptWithText(text) }
                } else {
                    // Silence / noise — restart shadow mic and keep monitoring
                    mainHandler.postDelayed({ startBargeInListening() }, 100)
                }
            },
            onError = {
                if (bargeInListening) mainHandler.postDelayed({ startBargeInListening() }, 500)
            }
        )
    }

    private fun startConversation() {
        conversationActive = true
        discardNextResult = false
        updateTalkButton()
        startListening()
    }

    private fun stopConversation() {
        conversationActive = false
        bargeInListening = false
        mainHandler.removeCallbacksAndMessages(null)
        when (viewModel.status.value) {
            ChatStatus.LISTENING -> {
                discardNextResult = true
                engine?.stopListening()
            }
            ChatStatus.SPEAKING -> engine?.cancelListening()  // cancel shadow barge-in mic
            else -> {}
        }
        viewModel.setStatus(ChatStatus.IDLE)
        updateTalkButton()
    }

    private fun startListening() {
        if (!conversationActive) return
        if (engine == null) engine = SpeechEngineFactory.create(this)
        viewModel.setStatus(ChatStatus.LISTENING)

        // holdMode=false: silence auto-fires onFinal.
        // SherpaOnnx fires after 1.5s of silence; AndroidSTT after 2.5s.
        engine?.startListening(
            holdMode  = false,
            onPartial = { },
            onFinal   = { text ->
                val discard = discardNextResult
                discardNextResult = false
                when {
                    discard                                        -> viewModel.setStatus(ChatStatus.IDLE)
                    text.isNotBlank() && !isSherpaArtifact(text)  -> viewModel.sendText(text)
                    else -> {
                        // Silence / noise / low-confidence — stay in loop, restart mic
                        viewModel.setStatus(ChatStatus.IDLE)
                        if (conversationActive) mainHandler.postDelayed({ startListening() }, 300)
                    }
                }
            },
            onError = {
                viewModel.setStatus(ChatStatus.IDLE)
                if (conversationActive) mainHandler.postDelayed({ startListening() }, 500)
            }
        )
    }

    // ── Mode toggle ───────────────────────────────────────────────────────────

    private fun setVoiceMode(voice: Boolean) {
        voiceMode = voice
        if (!voice && conversationActive) stopConversation()
        applyModeUi()
    }

    private fun applyModeUi() {
        if (voiceMode) {
            btnModeVoice.setBackgroundResource(R.drawable.pill_background_accent)
            btnModeVoice.setTextColor(getColor(R.color.jv_text))
            btnModeType.setBackgroundResource(R.drawable.pill_background_dim)
            btnModeType.setTextColor(getColor(R.color.jv_text2))
            btnTalk.visibility     = View.VISIBLE
            llTypeInput.visibility = View.GONE
            dismissKeyboard()
        } else {
            btnModeType.setBackgroundResource(R.drawable.pill_background_accent)
            btnModeType.setTextColor(getColor(R.color.jv_text))
            btnModeVoice.setBackgroundResource(R.drawable.pill_background_dim)
            btnModeVoice.setTextColor(getColor(R.color.jv_text2))
            btnTalk.visibility     = View.GONE
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
            popup.menu.add(0, i, i, name).isChecked = (name == viewModel.selectedModel.value)
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
                tvStreaming.text = text
                tvStreaming.visibility = if (text.isEmpty()) View.GONE else View.VISIBLE
            }
        }
        uiScope.launch {
            viewModel.toolStatusText.collect { text ->
                if (text.isNotEmpty()) tvStatus.text = text
            }
        }
        uiScope.launch {
            viewModel.status.collect { status ->
                tvStatus.text = when (status) {
                    ChatStatus.IDLE      -> ""
                    ChatStatus.LISTENING -> "Listening…"
                    ChatStatus.THINKING  -> "Thinking…"
                    ChatStatus.TOOL_CALL -> tvStatus.text  // toolStatusText flow owns this
                    ChatStatus.SPEAKING  -> "Speaking…"
                }

                if (conversationActive) updateTalkButton(status)

                // When Kai starts speaking, start the shadow mic so the user can barge in
                // by talking (AEC on the AudioRecord suppresses TTS bleed).
                if (status == ChatStatus.SPEAKING && conversationActive && voiceMode) {
                    bargeInListening = true
                    mainHandler.postDelayed({ startBargeInListening() }, 300)
                }

                // TTS finished naturally — cancel any in-flight shadow mic, restart normal listen.
                if (prevStatus == ChatStatus.SPEAKING
                        && status == ChatStatus.IDLE
                        && conversationActive
                        && voiceMode) {
                    bargeInListening = false
                    engine?.cancelListening()
                    mainHandler.postDelayed({ startListening() }, 600)
                }
                // LLM/tool call returned nothing — restart so the loop doesn't stall.
                if ((prevStatus == ChatStatus.THINKING || prevStatus == ChatStatus.TOOL_CALL)
                        && status == ChatStatus.IDLE
                        && conversationActive
                        && voiceMode) {
                    mainHandler.postDelayed({ startListening() }, 400)
                }
                prevStatus = status
            }
        }
        uiScope.launch {
            viewModel.selectedModel.collect { updateModelButton(it) }
        }
    }

    private fun updateTalkButton(currentStatus: ChatStatus = viewModel.status.value) {
        when {
            !conversationActive -> {
                btnTalk.setBackgroundResource(R.drawable.pill_background_accent)
                btnTalk.text  = "🎙  Talk to Kai"
            }
            currentStatus == ChatStatus.SPEAKING -> {
                btnTalk.setBackgroundResource(R.drawable.pill_background_stop)
                btnTalk.text  = "🎙  Interrupt Kai"
            }
            else -> {
                btnTalk.setBackgroundResource(R.drawable.pill_background_stop)
                btnTalk.text  = "⏹  End Conversation"
            }
        }
        btnTalk.alpha     = 1.0f
        btnTalk.isEnabled = true
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
        requestCode: Int, permissions: Array<out String>, grantResults: IntArray
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == REQ_MIC && grantResults.firstOrNull() == PackageManager.PERMISSION_GRANTED)
            startConversation()
    }

    // ── RecyclerView ──────────────────────────────────────────────────────────

    private inner class MessageAdapter : RecyclerView.Adapter<MessageViewHolder>() {
        private var items: List<ConversationMessage> = emptyList()
        fun update(newItems: List<ConversationMessage>) { items = newItems; notifyDataSetChanged() }
        override fun onCreateViewHolder(parent: ViewGroup, viewType: Int) =
            MessageViewHolder(LayoutInflater.from(parent.context)
                .inflate(R.layout.item_chat_message, parent, false))
        override fun onBindViewHolder(h: MessageViewHolder, pos: Int) = h.bind(items[pos])
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

    // SherpaOnnx emits tokens like "(static)", "[noise]", "(inaudible)" for non-speech audio.
    // Treat them as silence — don't send to the LLM.
    private fun isSherpaArtifact(text: String) =
        text.matches(Regex("""^\(.*\)$""")) || text.matches(Regex("""^\[.*]$"""))

    companion object { private const val REQ_MIC = 55 }
}
