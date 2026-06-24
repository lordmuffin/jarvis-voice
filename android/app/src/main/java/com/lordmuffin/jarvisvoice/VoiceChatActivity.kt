package com.lordmuffin.jarvisvoice

import android.Manifest
import android.app.Dialog
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.KeyEvent
import android.view.View
import android.view.ViewGroup
import android.view.Window
import android.view.inputmethod.EditorInfo
import android.view.inputmethod.InputMethodManager
import android.widget.Button
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.PopupMenu
import android.widget.ProgressBar
import android.widget.TextView
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import androidx.lifecycle.ViewModelProvider
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import android.view.LayoutInflater
import com.lordmuffin.jarvisvoice.notify.NotifyWorker
import com.lordmuffin.jarvisvoice.chat.ChatStatus
import com.lordmuffin.jarvisvoice.chat.ConversationMessage
import com.lordmuffin.jarvisvoice.chat.SessionMeta
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
    private lateinit var btnDelegate:  Button
    private lateinit var pbContext:        ProgressBar
    private lateinit var tvContextTokens:  TextView
    private lateinit var btnSessions:      Button
    private lateinit var btnNewConvo:      Button
    private lateinit var btnCompact:       Button

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
        btnSendText       = findViewById(R.id.btn_send_text)
        btnDelegate       = findViewById(R.id.btn_delegate)
        pbContext         = findViewById(R.id.pb_context)
        tvContextTokens   = findViewById(R.id.tv_context_tokens)
        btnSessions       = findViewById(R.id.btn_sessions)
        btnNewConvo       = findViewById(R.id.btn_new_convo)
        btnCompact        = findViewById(R.id.btn_compact)

        rvMessages.layoutManager = LinearLayoutManager(this).apply { stackFromEnd = true }
        rvMessages.adapter = adapter

        // Start background notification poller and request POST_NOTIFICATIONS permission
        NotifyWorker.schedule(this)
        if (android.os.Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
                != android.content.pm.PackageManager.PERMISSION_GRANTED) {
            ActivityCompat.requestPermissions(this, arrayOf(Manifest.permission.POST_NOTIFICATIONS), 99)
        }

        viewModel = ViewModelProvider(this)[VoiceChatViewModel::class.java]

        observeViewModel()

        btnModeVoice.setOnClickListener { setVoiceMode(true) }
        btnModeType.setOnClickListener  { setVoiceMode(false) }
        btnModel.setOnClickListener     { showModelMenu() }
        btnTalk.setOnClickListener      { onTalkTapped() }
        btnSendText.setOnClickListener  { sendTypedMessage() }
        btnDelegate.setOnClickListener  { delegateToAgent() }
        btnSessions.setOnClickListener  { showSessionsDialog() }
        btnNewConvo.setOnClickListener  {
            stopConversation()
            viewModel.clearHistory()
        }
        btnCompact.setOnClickListener   { viewModel.compactHistory() }

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
                // SherpaOnnx emits artifact tokens like "(static)", "[noise]", "(inaudible)"
                // for non-speech audio. Treat them the same as silence.
                val isArtifact = text.matches(Regex("""^\(.*\)$""")) ||
                                 text.matches(Regex("""^\[.*]$"""))
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
                // by talking. Skip auto-barge-in when Kokoro (NetworkTTS) is active —
                // hardware AEC only references VOICE_COMMUNICATION streams, not USAGE_ASSISTANT,
                // so Kokoro's speaker output bleeds through the mic and self-triggers. The user
                // can still tap btn_talk to interrupt manually.
                if (status == ChatStatus.SPEAKING && conversationActive && voiceMode
                        && !viewModel.hasNetworkTts()) {
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
        uiScope.launch {
            viewModel.contextTokens.collect { tokens ->
                pbContext.progress = tokens.coerceAtMost(pbContext.max)
                tvContextTokens.text = when {
                    tokens >= 1000 -> "${"%.1f".format(tokens / 1000.0)}k tkns"
                    else           -> "$tokens tkns"
                }
                // Colour the bar: green → yellow → red as context fills
                val tint = when {
                    tokens < 16_000 -> getColor(R.color.jv_accent)
                    tokens < 28_000 -> getColor(R.color.jv_warning)
                    else            -> getColor(R.color.jv_error)
                }
                pbContext.progressTintList =
                    android.content.res.ColorStateList.valueOf(tint)
            }
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

    // ── Delegate to agent ─────────────────────────────────────────────────────

    private fun delegateToAgent() {
        val text = etMessage.text.toString().trim()
        etMessage.setText("")
        dismissKeyboard()
        val intent = Intent(this, AgentTaskActivity::class.java).apply {
            putExtra(AgentTaskActivity.EXTRA_PROMPT, text)
            putExtra(AgentTaskActivity.EXTRA_MODEL, viewModel.selectedModel.value)
            flags = Intent.FLAG_ACTIVITY_REORDER_TO_FRONT
        }
        startActivity(intent)
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

    // ── Sessions dialog ───────────────────────────────────────────────────────

    private fun showSessionsDialog() {
        val dialog = Dialog(this)
        dialog.requestWindowFeature(Window.FEATURE_NO_TITLE)
        dialog.setContentView(R.layout.bottom_sheet_sessions)
        dialog.window?.apply {
            setLayout(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
            setGravity(Gravity.BOTTOM)
            setBackgroundDrawableResource(android.R.color.transparent)
        }

        val rv     = dialog.findViewById<RecyclerView>(R.id.rv_sessions)
        val btnNew = dialog.findViewById<Button>(R.id.btn_session_new)

        var sessionsAdapter: SessionsAdapter? = null

        uiScope.launch {
            viewModel.sessions.collect { sessions ->
                if (sessionsAdapter == null) {
                    sessionsAdapter = SessionsAdapter(
                        sessions   = sessions.toMutableList(),
                        activeId   = viewModel.activeSessionId.value,
                        onSwitch   = { id ->
                            stopConversation()
                            viewModel.switchSession(id)
                            dialog.dismiss()
                        },
                        onDelete   = { id -> viewModel.deleteSession(id) },
                        onRename   = { id, current -> showRenameDialog(id, current) },
                    )
                    rv.layoutManager = LinearLayoutManager(this@VoiceChatActivity)
                    rv.adapter = sessionsAdapter
                } else {
                    sessionsAdapter?.update(sessions, viewModel.activeSessionId.value)
                }
            }
        }

        btnNew.setOnClickListener {
            stopConversation()
            viewModel.newSession()
            dialog.dismiss()
        }

        dialog.show()
    }

    private fun showRenameDialog(id: String, current: String) {
        val et = EditText(this).apply {
            setText(current)
            selectAll()
            setPadding(48, 32, 48, 8)
        }
        AlertDialog.Builder(this)
            .setTitle("Rename conversation")
            .setView(et)
            .setPositiveButton("Save") { _, _ ->
                val name = et.text.toString().trim()
                if (name.isNotEmpty()) viewModel.renameSession(id, name)
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    // ── Sessions RecyclerView adapter ─────────────────────────────────────────

    private inner class SessionsAdapter(
        private val sessions: MutableList<SessionMeta>,
        private var activeId: String?,
        private val onSwitch: (String) -> Unit,
        private val onDelete: (String) -> Unit,
        private val onRename: (String, String) -> Unit,
    ) : RecyclerView.Adapter<SessionViewHolder>() {

        fun update(newSessions: List<SessionMeta>, newActiveId: String?) {
            sessions.clear()
            sessions.addAll(newSessions)
            activeId = newActiveId
            notifyDataSetChanged()
        }

        override fun onCreateViewHolder(parent: ViewGroup, viewType: Int) =
            SessionViewHolder(LayoutInflater.from(parent.context)
                .inflate(R.layout.item_chat_session, parent, false))

        override fun getItemCount() = sessions.size

        override fun onBindViewHolder(h: SessionViewHolder, pos: Int) {
            val s = sessions[pos]
            val isActive = s.id == activeId
            h.tvName.text    = s.name
            h.tvCount.text   = "${s.messageCount} msgs"
            h.tvPreview.text = s.preview.ifBlank { "No messages yet" }
            h.tvName.setTextColor(
                getColor(if (isActive) R.color.jv_accent else R.color.jv_text)
            )
            h.itemView.setOnClickListener { onSwitch(s.id) }
            h.itemView.setOnLongClickListener {
                showSessionActionMenu(s, h.itemView)
                true
            }
        }

        private fun showSessionActionMenu(s: SessionMeta, anchor: View) {
            val popup = PopupMenu(this@VoiceChatActivity, anchor)
            popup.menu.add(0, 0, 0, "Rename")
            popup.menu.add(0, 1, 1, "Delete")
            popup.setOnMenuItemClickListener { item ->
                when (item.itemId) {
                    0 -> onRename(s.id, s.name)
                    1 -> AlertDialog.Builder(this@VoiceChatActivity)
                            .setTitle("Delete conversation?")
                            .setMessage("\"${s.name}\" will be permanently removed.")
                            .setPositiveButton("Delete") { _, _ -> onDelete(s.id) }
                            .setNegativeButton("Cancel", null)
                            .show()
                }
                true
            }
            popup.show()
        }
    }

    private inner class SessionViewHolder(view: View) : RecyclerView.ViewHolder(view) {
        val tvName:    TextView = view.findViewById(R.id.tv_session_name)
        val tvCount:   TextView = view.findViewById(R.id.tv_session_count)
        val tvPreview: TextView = view.findViewById(R.id.tv_session_preview)
    }

    // SherpaOnnx emits tokens like "(static)", "[noise]", "(inaudible)" for non-speech audio.
    // Treat them as silence — don't send to the LLM.
    private fun isSherpaArtifact(text: String) =
        text.matches(Regex("""^\(.*\)$""")) || text.matches(Regex("""^\[.*]$"""))

    companion object { private const val REQ_MIC = 55 }
}
