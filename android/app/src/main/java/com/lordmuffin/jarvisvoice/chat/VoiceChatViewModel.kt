package com.lordmuffin.jarvisvoice.chat

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.lordmuffin.jarvisvoice.DebugLog
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

enum class ChatStatus { IDLE, LISTENING, THINKING, SPEAKING }

class VoiceChatViewModel(app: Application) : AndroidViewModel(app) {

    private val llm = LlmRepository()
    private val tts = TtsRepository(app.applicationContext)

    private val _messages     = MutableStateFlow<List<ConversationMessage>>(emptyList())
    private val _status       = MutableStateFlow(ChatStatus.IDLE)
    private val _streamingText = MutableStateFlow("")

    val messages:      StateFlow<List<ConversationMessage>> = _messages.asStateFlow()
    val status:        StateFlow<ChatStatus>                = _status.asStateFlow()
    val streamingText: StateFlow<String>                    = _streamingText.asStateFlow()

    private var activeJob: Job? = null

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
                        .onFailure { e -> DebugLog.e("VoiceChat/TTS", "speak failed", e) }
                }
                val full = response.toString().trim()
                if (full.isNotEmpty()) {
                    _messages.value = history + ConversationMessage("assistant", full)
                }
            } catch (e: Exception) {
                DebugLog.e("VoiceChat/LLM", "stream failed", e)
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

    override fun onCleared() {
        super.onCleared()
        tts.stop()
    }
}
