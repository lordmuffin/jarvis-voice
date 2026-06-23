package com.lordmuffin.jarvisvoice.chat

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.lordmuffin.jarvisvoice.DebugLog
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

enum class ChatStatus { IDLE, LISTENING, THINKING, SPEAKING }

class VoiceChatViewModel(app: Application) : AndroidViewModel(app) {

    private val llm = LlmRepository()
    private val tts = TtsRepository(app.applicationContext)

    private val _messages      = MutableStateFlow<List<ConversationMessage>>(emptyList())
    private val _status        = MutableStateFlow(ChatStatus.IDLE)
    private val _streamingText = MutableStateFlow("")
    private val _selectedModel = MutableStateFlow("local-default")
    private val _availableModels = MutableStateFlow<List<String>>(listOf("local-default"))

    val messages:        StateFlow<List<ConversationMessage>> = _messages.asStateFlow()
    val status:          StateFlow<ChatStatus>                = _status.asStateFlow()
    val streamingText:   StateFlow<String>                    = _streamingText.asStateFlow()
    val selectedModel:   StateFlow<String>                    = _selectedModel.asStateFlow()
    val availableModels: StateFlow<List<String>>              = _availableModels.asStateFlow()

    private var activeJob: Job? = null
    private val historyFile = File(app.filesDir, "voice_chat_history.json")

    init {
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
                // Keep selectedModel valid — if current selection not in list, reset to first
                if (_selectedModel.value !in models) {
                    _selectedModel.value = models.first()
                }
            }
        }
    }

    // ── History persistence ───────────────────────────────────────────────────

    private fun loadHistory() {
        viewModelScope.launch(Dispatchers.IO) {
            if (!historyFile.exists()) return@launch
            try {
                val arr = JSONArray(historyFile.readText())
                val msgs = (0 until arr.length()).map { i ->
                    val obj = arr.getJSONObject(i)
                    ConversationMessage(obj.getString("role"), obj.getString("content"))
                }
                _messages.value = msgs
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
            val response = StringBuilder()

            try {
                // Stream all tokens into the live preview, wait for the full
                // response before speaking so the user hears a complete reply.
                llm.streamChat(history, _selectedModel.value).collect { token ->
                    response.append(token)
                    _streamingText.value = response.toString()
                }

                val full = response.toString().trim()
                if (full.isNotEmpty()) {
                    val updated = history + ConversationMessage("assistant", full)
                    _messages.value = updated
                    saveHistory(updated)

                    _streamingText.value = ""
                    _status.value = ChatStatus.SPEAKING

                    // Android on-device TTS — no network, instant, no threading issues.
                    runCatching { tts.speak(full) }
                        .onFailure { e -> DebugLog.e("VoiceChat/TTS", "speak failed: ${e.message}") }
                }
            } catch (e: Exception) {
                DebugLog.e("VoiceChat/LLM", "stream failed: ${e.message}")
            } finally {
                _streamingText.value = ""
                _status.value = ChatStatus.IDLE
            }
        }
    }

    fun cancelActive() {
        activeJob?.cancel()
        tts.stop()
        _streamingText.value = ""
        _status.value = ChatStatus.IDLE
    }

    fun clearHistory() {
        _messages.value = emptyList()
        viewModelScope.launch(Dispatchers.IO) {
            runCatching { historyFile.delete() }
        }
    }

    override fun onCleared() {
        super.onCleared()
        tts.destroy()
    }
}
