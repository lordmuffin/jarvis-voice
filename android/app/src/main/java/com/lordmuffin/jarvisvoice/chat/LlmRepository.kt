package com.lordmuffin.jarvisvoice.chat

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okhttp3.sse.EventSource
import okhttp3.sse.EventSourceListener
import okhttp3.sse.EventSources
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.TimeUnit

private const val LLM_BASE      = "http://192.168.1.93:4000"
private const val DEFAULT_MODEL  = "local-default"
private const val SYSTEM_PROMPT  =
    "You are Kai, Andrew's AI chief of staff. Be concise and direct."

class LlmRepository {

    private val client = OkHttpClient.Builder()
        .readTimeout(60, TimeUnit.SECONDS)
        .connectTimeout(10, TimeUnit.SECONDS)
        .build()

    fun streamChat(
        history: List<ConversationMessage>,
        model: String = DEFAULT_MODEL
    ): Flow<String> = callbackFlow {
        val messages = JSONArray().apply {
            put(JSONObject().put("role", "system").put("content", SYSTEM_PROMPT))
            history.forEach { msg ->
                put(JSONObject().put("role", msg.role).put("content", msg.content))
            }
        }
        val body = JSONObject()
            .put("model", model)
            .put("messages", messages)
            .put("stream", true)
            .toString()
            .toRequestBody("application/json".toMediaType())

        val request = Request.Builder()
            .url("$LLM_BASE/v1/chat/completions")
            .post(body)
            .build()

        val listener = object : EventSourceListener() {
            override fun onEvent(source: EventSource, id: String?, type: String?, data: String) {
                if (data == "[DONE]") { close(); return }
                try {
                    val token = JSONObject(data)
                        .getJSONArray("choices")
                        .getJSONObject(0)
                        .getJSONObject("delta")
                        .optString("content", "")
                    if (token.isNotEmpty()) trySend(token)
                } catch (_: Exception) {}
            }

            override fun onFailure(source: EventSource, t: Throwable?, response: Response?) {
                close(t ?: IOException("SSE failed: ${response?.code}"))
            }

            override fun onClosed(source: EventSource) { close() }
        }

        val eventSource = EventSources.createFactory(client).newEventSource(request, listener)
        awaitClose { eventSource.cancel() }
    }

    // Fetches the list of model IDs available on the LiteLLM proxy.
    // Returns an empty list on network failure so callers can fall back to defaults.
    suspend fun fetchModels(): List<String> = withContext(Dispatchers.IO) {
        try {
            val request = Request.Builder().url("$LLM_BASE/v1/models").get().build()
            val response = client.newCall(request).execute()
            if (!response.isSuccessful) return@withContext emptyList()
            val body = response.body?.string() ?: return@withContext emptyList()
            val data = JSONObject(body).getJSONArray("data")
            (0 until data.length()).map { data.getJSONObject(it).getString("id") }.sorted()
        } catch (_: Exception) {
            emptyList()
        }
    }
}
