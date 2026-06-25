package com.lordmuffin.jarvisvoice.wear.sync

import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import com.lordmuffin.jarvisvoice.wear.db.RecordingStore
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.asRequestBody
import org.json.JSONObject
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.TimeUnit

private val ISO_FMT = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'", Locale.US)

class UploadWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {

    private val prefs = ctx.getSharedPreferences(PREF_FILE, Context.MODE_PRIVATE)
    private val client = OkHttpClient.Builder()
        .connectTimeout(30, TimeUnit.SECONDS)
        .readTimeout(120, TimeUnit.SECONDS)
        .build()

    override suspend fun doWork(): Result {
        val serverUrl = prefs.getString(PREF_SERVER_URL, DEFAULT_SERVER_URL) ?: DEFAULT_SERVER_URL
        val apiKey = prefs.getString(PREF_API_KEY, "") ?: ""
        if (apiKey.isBlank()) return Result.failure()

        val store = RecordingStore.get(applicationContext)
        val pending = store.getPending()
        if (pending.isEmpty()) return Result.success()

        var anyFailure = false
        for (recording in pending) {
            val file = File(recording.filePath)
            if (!file.exists()) {
                store.markFailed(recording.id, "Audio file missing: ${recording.filePath}")
                continue
            }

            try {
                val timestamp = ISO_FMT.format(Date(recording.timestampMs))
                val body = MultipartBody.Builder()
                    .setType(MultipartBody.FORM)
                    .addFormDataPart("file", file.name, file.asRequestBody("audio/wav".toMediaType()))
                    .addFormDataPart("device", "wear")
                    .addFormDataPart("timestamp", timestamp)
                    .build()

                val req = Request.Builder()
                    .url("$serverUrl/api/v1/capture/audio")
                    .header("X-Jarvis-Key", apiKey)
                    .post(body)
                    .build()

                val resp = client.newCall(req).execute()
                val bodyStr = resp.body?.string() ?: "{}"
                if (resp.isSuccessful) {
                    val responseText = runCatching {
                        JSONObject(bodyStr).optString("response", "")
                    }.getOrDefault("")
                    store.markSynced(recording.id, responseText)
                    file.delete()
                } else {
                    store.markFailed(recording.id, "HTTP ${resp.code}: $bodyStr")
                    anyFailure = true
                }
            } catch (e: Exception) {
                store.markFailed(recording.id, e.message ?: "Upload error")
                anyFailure = true
            }
        }

        store.pruneOldSynced(System.currentTimeMillis() - 7 * 24 * 60 * 60 * 1000L)

        return if (anyFailure) Result.retry() else Result.success()
    }

    companion object {
        const val PREF_FILE = "jarvis_wear_prefs"
        const val PREF_API_KEY = "api_key"
        const val PREF_SERVER_URL = "server_url"
        const val DEFAULT_SERVER_URL = "https://kai.apj.dev"
        const val WORK_TAG = "jarvis_wear_upload"
    }
}
