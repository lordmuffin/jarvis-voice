package com.lordmuffin.jarvisvoice.call

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import okhttp3.Response
import okhttp3.sse.EventSource
import okhttp3.sse.EventSourceListener
import org.json.JSONObject

data class TranscriptLine(val role: String, val text: String)

enum class CallStatus { IDLE, CONNECTING, ACTIVE, ENDED }

class CallViewModel(app: Application) : AndroidViewModel(app) {

    private val repo = CallRepository(app.applicationContext)

    private val _status    = MutableStateFlow(CallStatus.IDLE)
    val status: StateFlow<CallStatus> = _status

    private val _statusText = MutableStateFlow("Ready")
    val statusText: StateFlow<String> = _statusText

    private val _transcript = MutableStateFlow<List<TranscriptLine>>(emptyList())
    val transcript: StateFlow<List<TranscriptLine>> = _transcript

    private val _error = MutableStateFlow<String?>(null)
    val error: StateFlow<String?> = _error

    private var callSid: String? = null
    private var eventSource: EventSource? = null

    fun initiateCall(to: String, contact: String, context: String) {
        if (_status.value != CallStatus.IDLE) return
        _status.value = CallStatus.CONNECTING
        _statusText.value = "Connecting…"
        _transcript.value = emptyList()

        viewModelScope.launch(Dispatchers.IO) {
            val result = repo.initiateCall(to, contact, context)
            result.onSuccess { sid ->
                callSid = sid
                _statusText.value = "Ringing…"
                subscribeTranscript(sid)
            }.onFailure { e ->
                _status.value = CallStatus.IDLE
                _statusText.value = "Ready"
                _error.value = "Failed to start call: ${e.message}"
            }
        }
    }

    fun endCall() {
        val sid = callSid ?: return
        viewModelScope.launch(Dispatchers.IO) {
            repo.endCall(sid)
        }
        eventSource?.cancel()
        _status.value = CallStatus.ENDED
        _statusText.value = "Call ended"
    }

    private fun subscribeTranscript(sid: String) {
        eventSource = repo.subscribeTranscript(sid, object : EventSourceListener() {
            override fun onEvent(es: EventSource, id: String?, type: String?, data: String) {
                try {
                    val json = JSONObject(data)
                    when (json.optString("type")) {
                        "status"    -> {
                            val s = json.optString("status")
                            _statusText.value = s.replaceFirstChar { it.titlecase() }
                            if (s == "in-progress" || s == "connected") {
                                _status.value = CallStatus.ACTIVE
                            }
                        }
                        "transcript" -> {
                            val line = TranscriptLine(
                                role = json.optString("role"),
                                text = json.optString("text"),
                            )
                            _transcript.value = _transcript.value + line
                        }
                        "ended" -> {
                            _status.value = CallStatus.ENDED
                            _statusText.value = "Call ended"
                            eventSource?.cancel()
                        }
                    }
                } catch (_: Exception) {}
            }

            override fun onFailure(es: EventSource, t: Throwable?, resp: Response?) {
                if (_status.value == CallStatus.ACTIVE) {
                    _status.value = CallStatus.ENDED
                    _statusText.value = "Connection lost"
                }
            }
        })
    }

    override fun onCleared() {
        super.onCleared()
        eventSource?.cancel()
    }
}
