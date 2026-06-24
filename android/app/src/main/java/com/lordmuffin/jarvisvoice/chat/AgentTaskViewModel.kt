package com.lordmuffin.jarvisvoice.chat

import android.app.Application
import android.content.Context
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.lordmuffin.jarvisvoice.VoiceOverlayService
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

class AgentTaskViewModel(app: Application) : AndroidViewModel(app) {

    private val repo = AgentTaskRepository()
    private val llm  = LlmRepository()

    private val _tasks          = MutableStateFlow<List<AgentTask>>(emptyList())
    private val _loading        = MutableStateFlow(false)
    private val _error          = MutableStateFlow<String?>(null)
    private val _availableModels = MutableStateFlow<List<String>>(listOf("local-default"))

    val tasks:           StateFlow<List<AgentTask>> = _tasks.asStateFlow()
    val loading:         StateFlow<Boolean>         = _loading.asStateFlow()
    val error:           StateFlow<String?>         = _error.asStateFlow()
    val availableModels: StateFlow<List<String>>    = _availableModels.asStateFlow()

    private var pollJob: Job? = null

    init {
        val prefs = app.getSharedPreferences(VoiceOverlayService.PREF_FILE, Context.MODE_PRIVATE)
        val key   = (prefs.getString(VoiceChatViewModel.PREF_VAULT_KEY, "") ?: "")
            .ifBlank { VoiceChatViewModel.DEFAULT_VAULT_KEY }
        repo.vaultApiKey = key
        llm.vaultApiKey  = key
        fetchModels()
        refresh()
        startPolling()
    }

    private fun fetchModels() {
        viewModelScope.launch {
            val models = llm.fetchModels()
            if (models.isNotEmpty()) _availableModels.value = models
        }
    }

    fun refresh() {
        viewModelScope.launch {
            _loading.value = true
            _error.value   = null
            val list = repo.listTasks()
            _tasks.value   = list
            _loading.value = false
        }
    }

    fun createTask(name: String, prompt: String, model: String, system: String = "") {
        viewModelScope.launch {
            val id = repo.createTask(name, prompt, model, system)
            if (id != null) {
                refresh()
            } else {
                _error.value = "Failed to create task — check server connectivity"
            }
        }
    }

    fun deleteTask(id: String) {
        viewModelScope.launch {
            repo.deleteTask(id)
            _tasks.value = _tasks.value.filter { it.id != id }
        }
    }

    fun startPolling() {
        pollJob?.cancel()
        pollJob = viewModelScope.launch {
            while (true) {
                delay(5_000)
                // Only poll if any task is still active
                if (_tasks.value.any { !it.isTerminal }) {
                    val list = repo.listTasks()
                    if (list.isNotEmpty()) _tasks.value = list
                }
            }
        }
    }

    fun stopPolling() { pollJob?.cancel() }

    override fun onCleared() {
        super.onCleared()
        stopPolling()
    }
}
