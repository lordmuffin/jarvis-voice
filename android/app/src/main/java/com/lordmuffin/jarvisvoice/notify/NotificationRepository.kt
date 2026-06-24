package com.lordmuffin.jarvisvoice.notify

import android.content.Context
import com.lordmuffin.jarvisvoice.VoiceOverlayService
import com.lordmuffin.jarvisvoice.chat.VoiceChatViewModel
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.util.concurrent.TimeUnit

class NotificationRepository(context: Context) {

    private val prefs = context.getSharedPreferences(VoiceOverlayService.PREF_FILE, Context.MODE_PRIVATE)
    private val apiKey get() = (prefs.getString(VoiceChatViewModel.PREF_VAULT_KEY, "") ?: "")
        .ifBlank { VoiceChatViewModel.DEFAULT_VAULT_KEY }
    private val base = "http://192.168.1.155:8881"

    private val http = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(15, TimeUnit.SECONDS)
        .build()

    fun fetchPending(): List<AppNotification> {
        val req = Request.Builder()
            .url("$base/api/v1/notify/pending")
            .addHeader("x-jarvis-key", apiKey)
            .get()
            .build()
        return try {
            http.newCall(req).execute().use { resp ->
                if (!resp.isSuccessful) return emptyList()
                val body = resp.body?.string() ?: return emptyList()
                val arr = JSONObject(body).getJSONArray("notifications")
                (0 until arr.length()).map { i ->
                    val o = arr.getJSONObject(i)
                    AppNotification(
                        id = o.getString("id"),
                        title = o.getString("title"),
                        body = o.optString("body", ""),
                        firesAt = (o.getDouble("fires_at") * 1000).toLong(),
                    )
                }
            }
        } catch (_: Exception) {
            emptyList()
        }
    }

    fun schedule(title: String, body: String, delayMinutes: Double): String? {
        val json = JSONObject().apply {
            put("title", title)
            put("body", body)
            put("delay_minutes", delayMinutes)
        }.toString()
        val req = Request.Builder()
            .url("$base/api/v1/notify/schedule")
            .addHeader("x-jarvis-key", apiKey)
            .post(json.toRequestBody("application/json".toMediaType()))
            .build()
        return try {
            http.newCall(req).execute().use { resp ->
                if (!resp.isSuccessful) return null
                val b = resp.body?.string() ?: return null
                JSONObject(b).getString("id")
            }
        } catch (_: Exception) {
            null
        }
    }
}
