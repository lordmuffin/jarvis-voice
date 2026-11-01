package com.lordmuffin.jarvisvoice.call

import android.content.Context
import com.lordmuffin.jarvisvoice.VoiceOverlayService
import com.lordmuffin.jarvisvoice.chat.VoiceChatViewModel
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.sse.EventSource
import okhttp3.sse.EventSourceListener
import okhttp3.sse.EventSources
import org.json.JSONObject
import java.util.concurrent.TimeUnit

private const val BASE_URL = "http://192.168.1.155:8881"

class CallRepository(private val context: Context) {

    private val prefs get() = context.getSharedPreferences(VoiceOverlayService.PREF_FILE, Context.MODE_PRIVATE)
    private val apiKey get() = prefs.getString(VoiceChatViewModel.PREF_VAULT_KEY, "") ?: ""

    private val client = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.SECONDS)
        .build()

    fun initiateCall(to: String, contact: String, context: String): Result<String> {
        return try {
            val body = JSONObject().apply {
                put("to", to)
                put("contact", contact)
                put("context", context)
            }.toString().toRequestBody("application/json".toMediaType())

            val req = Request.Builder()
                .url("$BASE_URL/api/v1/calls/initiate")
                .post(body)
                .header("X-Jarvis-Key", apiKey)
                .build()

            client.newCall(req).execute().use { resp ->
                if (!resp.isSuccessful) return Result.failure(Exception("HTTP ${resp.code}"))
                val json = JSONObject(resp.body?.string() ?: "{}")
                Result.success(json.optString("call_sid", ""))
            }
        } catch (e: Exception) {
            Result.failure(e)
        }
    }

    fun endCall(callSid: String): Result<Unit> {
        return try {
            val req = Request.Builder()
                .url("$BASE_URL/api/v1/calls/end/$callSid")
                .post("".toRequestBody())
                .header("X-Jarvis-Key", apiKey)
                .build()
            client.newCall(req).execute().use { Result.success(Unit) }
        } catch (e: Exception) {
            Result.failure(e)
        }
    }

    fun subscribeTranscript(callSid: String, listener: EventSourceListener): EventSource {
        val req = Request.Builder()
            .url("$BASE_URL/api/v1/calls/transcript/$callSid")
            .header("X-Jarvis-Key", apiKey)
            .build()
        return EventSources.createFactory(client).newEventSource(req, listener)
    }
}
