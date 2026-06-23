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
import kotlinx.coroutines.withContext
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

    val messages:      StateFlow<List<ConversationMessage>> = _messages.asStateFlow()
    val status:        StateFlow<ChatStatus>                = _status.asStateFlow()
    val streamingText: StateFlow<String>                    = _streamingText.asStateFlow()

    private var activeJob: Job? = null
    private val historyFile = File(app.filesDir, "voice_chat_history.json")

    init {
        loadHistory()
    }

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

    fun setStatus(s: ChatStatus) { _status.value = s }

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
                val sentences = splitIntoSentences(llm.streamChat(history))
                sentences.collect { sentence ->
                    response.append(sentence).append(" ")
                    _streamingText.value = response.toString().trim()
                    _status.value = ChatStatus.SPEAKING
                    runCatching { tts.speak(sentence) }
                        .onFailure { e -> DebugLog.e("VoiceChat/TTS", "speak failed: ${e.message}") }
                }
                val full = response.toString().trim()
                if (full.isNotEmpty()) {
                    val updated = history + ConversationMessage("assistant", full)
                    _messages.value = updated
                    saveHistory(updated)
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
        tts.stop()
    }
}
