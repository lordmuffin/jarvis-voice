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
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

enum class ChatStatus { IDLE, LISTENING, THINKING, TOOL_CALL, SPEAKING }

// Human-readable label shown in tvStatus during a tool call
private fun toolLabel(name: String) = when (name) {
    "read_note"      -> "Reading vault…"
    "search_vault"   -> "Searching vault…"
    "get_sprint_state" -> "Checking sprint board…"
    "append_to_note" -> "Writing to vault…"
    else             -> "Using vault…"
}

class VoiceChatViewModel(app: Application) : AndroidViewModel(app) {

    private val llm = LlmRepository()
    private val tts: TtsRepository
    private var networkTts: NetworkTtsRepository? = null

    private val _messages        = MutableStateFlow<List<ConversationMessage>>(emptyList())
    private val _status          = MutableStateFlow(ChatStatus.IDLE)
    private val _streamingText   = MutableStateFlow("")
    private val _toolStatusText  = MutableStateFlow("")
    private val _selectedModel   = MutableStateFlow("local-default")
    private val _availableModels = MutableStateFlow<List<String>>(listOf("local-default"))

    val messages:       StateFlow<List<ConversationMessage>> = _messages.asStateFlow()
    val status:         StateFlow<ChatStatus>                = _status.asStateFlow()
    val streamingText:  StateFlow<String>                    = _streamingText.asStateFlow()
    val toolStatusText: StateFlow<String>                    = _toolStatusText.asStateFlow()
    val selectedModel:  StateFlow<String>                    = _selectedModel.asStateFlow()
    val availableModels:StateFlow<List<String>>              = _availableModels.asStateFlow()

    private var activeJob: Job? = null
    private val historyFile = File(app.filesDir, "voice_chat_history.json")

    init {
        val prefs = app.getSharedPreferences(VoiceOverlayService.PREF_FILE, Context.MODE_PRIVATE)
        val storedKey = prefs.getString(PREF_VAULT_KEY, "") ?: ""
        llm.vaultApiKey = storedKey.ifBlank { DEFAULT_VAULT_KEY }

        // Use Kokoro network TTS when a URL is configured; fall back to Android TTS.
        val kokoroUrl = prefs.getString(PREF_TTS_URL, "") ?: ""
        val kokoroVoice = prefs.getString(PREF_TTS_VOICE, DEFAULT_TTS_VOICE) ?: DEFAULT_TTS_VOICE
        tts = TtsRepository(app.applicationContext)
        if (kokoroUrl.isNotBlank()) {
            networkTts = NetworkTtsRepository(kokoroUrl, kokoroVoice)
            DebugLog.i("VoiceChat", "Network TTS enabled: $kokoroUrl voice=$kokoroVoice")
        }

        loadHistory()
        fetchModels()
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

    // ── History persistence ───────────────────────────────────────────────────

    private fun loadHistory() {
        viewModelScope.launch(Dispatchers.IO) {
            if (!historyFile.exists()) return@launch
            try {
                val arr = JSONArray(historyFile.readText())
                _messages.value = (0 until arr.length()).map { i ->
                    val o = arr.getJSONObject(i)
                    ConversationMessage(o.getString("role"), o.getString("content"))
                }
            } catch (e: Exception) {
                DebugLog.e("VoiceChat", "load history failed: ${e.message}")
            }
        }
    }

    private fun saveHistory(messages: List<ConversationMessage>) {
        viewModelScope.launch(Dispatchers.IO) {
            try {
                val arr = JSONArray()
                messages.takeLast(50).forEach { msg ->
                    arr.put(JSONObject().put("role", msg.role).put("content", msg.content))
                }
                historyFile.writeText(arr.toString())
            } catch (e: Exception) {
                DebugLog.e("VoiceChat", "save history failed: ${e.message}")
            }
        }
    }

    // ── Status ────────────────────────────────────────────────────────────────

    fun setStatus(s: ChatStatus) { _status.value = s }

    // ── Chat ──────────────────────────────────────────────────────────────────

    fun sendText(userText: String) {
        if (userText.isBlank()) return

        val userMsg = ConversationMessage("user", userText.trim())
        val history = _messages.value.takeLast(10) + userMsg
        _messages.value = history

        activeJob?.cancel()
        activeJob = viewModelScope.launch {
            _status.value = ChatStatus.THINKING
            _streamingText.value = ""
            _toolStatusText.value = ""

            try {
                val full = llm.chatWithTools(
                    history  = history,
                    model    = _selectedModel.value,
                    onToolCall = { toolName ->
                        _status.value = ChatStatus.TOOL_CALL
                        _toolStatusText.value = toolLabel(toolName)
                    },
                )

                if (full.isNotEmpty()) {
                    val updated = history + ConversationMessage("assistant", full)
                    _messages.value = updated
                    saveHistory(updated)

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

    fun clearHistory() {
        _messages.value = emptyList()
        viewModelScope.launch(Dispatchers.IO) { runCatching { historyFile.delete() } }
    }

    override fun onCleared() {
        super.onCleared()
        networkTts?.destroy()
        tts.destroy()
    }

    companion object {
        const val PREF_VAULT_KEY    = "vault_api_key"
        const val PREF_TTS_URL      = "tts_url"
        const val PREF_TTS_VOICE    = "tts_voice"
        const val DEFAULT_TTS_VOICE = "af_sky"
        // Baked-in default — matches the server's _DEFAULT_API_KEY in capture_api.py.
        // Change both sides together if you rotate the key.
        const val DEFAULT_VAULT_KEY = "0WBpWVdLsieaJPpTI7JEjKBZZMd2G-9WWZM2Iiq_wMo"
    }
}
