package com.lordmuffin.jarvisvoice.wearcommon

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.asRequestBody
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.util.concurrent.TimeUnit

/**
 * `POST /v1/sessions/upload` (`server/src/jarvis_live/api/sessions.py`): a recorded file as the raw
 * body, authenticated with the same device token as the rest of the Live API. Used by the phone
 * (for what the watch sent it) and by the watch (when it uploads on its own).
 *
 * `client_id` makes a retry idempotent: if the response is lost, uploading again returns the same
 * session instead of a duplicate. A 2xx is the server's ack.
 */
class RecordingUploader(
    baseUrl: String,
    private val deviceToken: String,
    private val http: OkHttpClient = defaultClient(),
) {
    private val base = baseUrl.trim().trimEnd('/').toHttpUrl()

    suspend fun upload(file: File, clientId: String, title: String?, source: String): UploadResult =
        withContext(Dispatchers.IO) {
            val url = base.newBuilder().addPathSegments("v1/sessions/upload")
                .addQueryParameter("source", source)
                .addQueryParameter("client_id", clientId)
                .addQueryParameter("filename", file.name)
                .apply { title?.takeIf { it.isNotBlank() }?.let { addQueryParameter("title", it) } }
                .build()
            val req = Request.Builder().url(url)
                .header("Authorization", "Bearer $deviceToken")
                .post(file.asRequestBody("application/octet-stream".toMediaType()))
                .build()
            try {
                http.newCall(req).execute().use { res ->
                    val text = res.body?.string().orEmpty()
                    classify(res.code, text)
                }
            } catch (e: IOException) {
                UploadResult.Retry(e.message ?: "network error")
            }
        }

    companion object {
        fun classify(code: Int, body: String): UploadResult = when {
            code in 200..299 -> {
                val id = runCatching { JSONObject(body).getString("id") }.getOrNull()
                // A 2xx without a session id is not the Jarvis server (a captive portal, a proxy).
                if (id.isNullOrBlank()) UploadResult.Retry("unexpected response") else UploadResult.Acked(id)
            }
            code == 401 || code == 403 -> UploadResult.AuthRejected("HTTP $code")
            code == 413 || code == 415 || code == 422 -> UploadResult.Rejected("HTTP $code: ${body.take(200)}")
            else -> UploadResult.Retry("HTTP $code")
        }

        fun defaultClient(): OkHttpClient = OkHttpClient.Builder()
            .connectTimeout(15, TimeUnit.SECONDS)
            .writeTimeout(60, TimeUnit.SECONDS)
            // The server decodes the audio before it answers.
            .readTimeout(120, TimeUnit.SECONDS)
            .build()
    }
}
