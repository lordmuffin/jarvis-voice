package com.lordmuffin.jarvisvoice.chat

import android.app.Application
import android.content.Context
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.lordmuffin.jarvisvoice.DebugLog
import com.lordmuffin.jarvisvoice.VoiceOverlayService
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

enum class ChatStatus { IDLE, LISTENING, THINKING, TOOL_CALL, SPEAKING }

private fun toolLabel(name: String) = when (name) {
    "read_note"        -> "Reading vault…"
    "search_vault"     -> "Searching vault…"
    "get_sprint_state" -> "Checking sprint board…"
    "append_to_note"   -> "Writing to vault…"
    "write_note"       -> "Saving note…"
    "web_fetch"        -> "Fetching page…"
    "run_command"      -> "Running command…"
    else               -> "Using tools…"
}

class VoiceChatViewModel(app: Application) : AndroidViewModel(app) {

    private val llm            = LlmRepository(app.applicationContext)
    private val tts: TtsRepository
    private var networkTts: NetworkTtsRepository? = null
    private val sessionManager = SessionManager(app.applicationContext)

    private val _messages        = MutableStateFlow<List<ConversationMessage>>(emptyList())
    private val _status          = MutableStateFlow(ChatStatus.IDLE)
    private val _streamingText   = MutableStateFlow("")
    private val _toolStatusText  = MutableStateFlow("")
    private val _selectedModel   = MutableStateFlow("local-default")
    private val _availableModels = MutableStateFlow<List<String>>(listOf("local-default"))
    private val _contextTokens   = MutableStateFlow(0)
    private val _sessions        = MutableStateFlow<List<SessionMeta>>(emptyList())
    private val _activeSessionId = MutableStateFlow<String?>(null)

    val messages:        StateFlow<List<ConversationMessage>> = _messages.asStateFlow()
    val status:          StateFlow<ChatStatus>                = _status.asStateFlow()
    val streamingText:   StateFlow<String>                    = _streamingText.asStateFlow()
    val toolStatusText:  StateFlow<String>                    = _toolStatusText.asStateFlow()
    val selectedModel:   StateFlow<String>                    = _selectedModel.asStateFlow()
    val availableModels: StateFlow<List<String>>              = _availableModels.asStateFlow()
    val contextTokens:   StateFlow<Int>                       = _contextTokens.asStateFlow()
    val sessions:        StateFlow<List<SessionMeta>>         = _sessions.asStateFlow()
    val activeSessionId: StateFlow<String?>                   = _activeSessionId.asStateFlow()

    private var activeJob: Job? = null

    init {
        val prefs = app.getSharedPreferences(VoiceOverlayService.PREF_FILE, Context.MODE_PRIVATE)
        val storedKey = prefs.getString(PREF_VAULT_KEY, "") ?: ""
        llm.vaultApiKey = storedKey.ifBlank { DEFAULT_VAULT_KEY }

        val kokoroUrl   = (prefs.getString(PREF_TTS_URL, "") ?: "").ifBlank { DEFAULT_TTS_URL }
        val kokoroVoice = prefs.getString(PREF_TTS_VOICE, DEFAULT_TTS_VOICE) ?: DEFAULT_TTS_VOICE
        tts = TtsRepository(app.applicationContext)
        if (kokoroUrl.isNotBlank()) {
            networkTts = NetworkTtsRepository(app.applicationContext, kokoroUrl, kokoroVoice)
            DebugLog.i("VoiceChat", "Network TTS enabled: $kokoroUrl voice=$kokoroVoice")
        }

        loadSessionList()
        fetchModels()
    }

    // ── Session management ────────────────────────────────────────────────────

    private fun loadSessionList() {
        viewModelScope.launch(Dispatchers.IO) {
            val list = sessionManager.listSessions()
            _sessions.value = list
            if (list.isNotEmpty()) {
                val id = list.first().id
                _activeSessionId.value = id
                _messages.value = sessionManager.loadMessages(id)
            } else {
                // First launch — create an initial session
                val meta = sessionManager.createSession()
                _sessions.value = listOf(meta)
                _activeSessionId.value = meta.id
                _messages.value = emptyList()
            }
        }
    }

    fun newSession() {
        viewModelScope.launch(Dispatchers.IO) {
            val meta = sessionManager.createSession()
            val list = sessionManager.listSessions()
            _sessions.value     = list
            _activeSessionId.value = meta.id
            _messages.value     = emptyList()
            _contextTokens.value = 0
        }
    }

    fun switchSession(id: String) {
        if (id == _activeSessionId.value) return
        viewModelScope.launch(Dispatchers.IO) {
            _activeSessionId.value = id
            _messages.value     = sessionManager.loadMessages(id)
            _contextTokens.value = 0
        }
    }

    fun deleteSession(id: String) {
        viewModelScope.launch(Dispatchers.IO) {
            sessionManager.deleteSession(id)
            val list = sessionManager.listSessions()
            _sessions.value = list
            if (_activeSessionId.value == id) {
                if (list.isNotEmpty()) {
                    _activeSessionId.value = list.first().id
                    _messages.value = sessionManager.loadMessages(list.first().id)
                } else {
                    val meta = sessionManager.createSession()
                    _sessions.value = listOf(meta)
                    _activeSessionId.value = meta.id
                    _messages.value = emptyList()
                }
                _contextTokens.value = 0
            }
        }
    }

    fun renameSession(id: String, name: String) {
        viewModelScope.launch(Dispatchers.IO) {
            sessionManager.renameSession(id, name)
            _sessions.value = sessionManager.listSessions()
        }
    }

    // ── Model management ──────────────────────────────────────────────────────

    fun selectModel(model: String) {
        _selectedModel.value = model
        DebugLog.i("VoiceChat", "Model switched to: $model")
    }

    private fun fetchModels() {
        viewModelScope.launch(Dispatchers.IO) {
            val models = llm.fetchModels()
            if (models.isNotEmpty()) {
                _availableModels.value = models
                if (_selectedModel.value !in models) _selectedModel.value = models.first()
            }
        }
    }

    // ── Status ────────────────────────────────────────────────────────────────

    fun setStatus(s: ChatStatus) { _status.value = s }

    // ── Chat ──────────────────────────────────────────────────────────────────

    fun sendText(userText: String) {
        if (userText.isBlank()) return

        val userMsg = ConversationMessage("user", userText.trim())
        val allMessages = _messages.value + userMsg
        _messages.value = allMessages
        persistCurrentSession(allMessages)

        // Auto-name the session from the first user message
        val sessionId = _activeSessionId.value ?: return
        if (allMessages.size == 1) {
            viewModelScope.launch(Dispatchers.IO) {
                sessionManager.renameSession(sessionId, autoSessionName(userText))
                _sessions.value = sessionManager.listSessions()
            }
        }

        activeJob?.cancel()
        activeJob = viewModelScope.launch {
            _status.value = ChatStatus.THINKING
            _streamingText.value = ""
            _toolStatusText.value = ""

            try {
                val full = llm.chatWithTools(
                    history    = allMessages.takeLast(10),
                    model      = _selectedModel.value,
                    onToolCall = { toolName ->
                        _status.value = ChatStatus.TOOL_CALL
                        _toolStatusText.value = toolLabel(toolName)
                    },
                    onUsage = { tokens -> _contextTokens.value = tokens },
                )

                if (full.isNotEmpty()) {
                    val updated = _messages.value + ConversationMessage("assistant", full)
                    _messages.value = updated
                    persistCurrentSession(updated)

                    _streamingText.value = ""
                    _toolStatusText.value = ""
                    _status.value = ChatStatus.SPEAKING

                    runCatching {
                        val net = networkTts
                        if (net != null) net.speak(full) else tts.speak(full)
                    }.onFailure { e -> DebugLog.e("VoiceChat/TTS", "speak failed: ${e.message}") }
                }
            } catch (e: Exception) {
                DebugLog.e("VoiceChat/LLM", "chat failed: ${e.message}")
            } finally {
                _streamingText.value = ""
                _toolStatusText.value = ""
                _status.value = ChatStatus.IDLE
            }
        }
    }

    fun hasNetworkTts(): Boolean = networkTts != null

    fun cancelActive() {
        activeJob?.cancel()
        networkTts?.stop() ?: tts.stop()
        _streamingText.value = ""
        _toolStatusText.value = ""
        _status.value = ChatStatus.IDLE
    }

    fun interruptSpeaking() {
        activeJob?.cancel()
        networkTts?.stop() ?: tts.stop()
        _streamingText.value = ""
        _toolStatusText.value = ""
        _status.value = ChatStatus.IDLE
    }

    // "New" button in context bar — creates a fresh session
    fun clearHistory() {
        newSession()
    }

    fun compactHistory() {
        val msgs = _messages.value
        if (msgs.size < 4) return
        activeJob?.cancel()
        activeJob = viewModelScope.launch {
            _status.value = ChatStatus.THINKING
            _toolStatusText.value = "Compacting context…"
            try {
                val summaryRequest = msgs + ConversationMessage(
                    "user",
                    "Summarize this entire conversation in 3-5 sentences covering the key topics, " +
                    "decisions, and outcomes. Return only the summary text, nothing else."
                )
                val summary = llm.chatWithTools(
                    history = summaryRequest.takeLast(30),
                    model   = _selectedModel.value,
                )
                if (summary.isNotBlank()) {
                    val compacted = listOf(
                        ConversationMessage(
                            "assistant",
                            "[Context compacted — summary of prior conversation]\n\n$summary"
                        )
                    )
                    _messages.value = compacted
                    persistCurrentSession(compacted)
                    _contextTokens.value = 0
                }
            } catch (e: Exception) {
                DebugLog.e("VoiceChat", "compact failed: ${e.message}")
            } finally {
                _toolStatusText.value = ""
                _status.value = ChatStatus.IDLE
            }
        }
    }

    override fun onCleared() {
        super.onCleared()
        networkTts?.destroy()
        tts.destroy()
    }

    // ── Internal ──────────────────────────────────────────────────────────────

    private fun persistCurrentSession(messages: List<ConversationMessage>) {
        val id = _activeSessionId.value ?: return
        viewModelScope.launch(Dispatchers.IO) {
            sessionManager.saveMessages(id, messages)
            _sessions.value = sessionManager.listSessions()
        }
    }

    private fun autoSessionName(firstMessage: String): String {
        val words = firstMessage.trim().split(Regex("\\s+"))
        return words.take(5).joinToString(" ").take(40)
    }

    companion object {
        const val PREF_VAULT_KEY    = "vault_api_key"
        const val PREF_TTS_URL      = "tts_url"
        const val PREF_TTS_VOICE    = "tts_voice"
        const val DEFAULT_TTS_VOICE = "af_sky"
        const val DEFAULT_TTS_URL   = "http://192.168.1.43:8880"
        const val DEFAULT_VAULT_KEY = "0WBpWVdLsieaJPpTI7JEjKBZZMd2G-9WWZM2Iiq_wMo"
    }
}
