package com.lordmuffin.jarvisvoice.live

import com.lordmuffin.jarvisvoice.live.protocol.Channel
import com.lordmuffin.jarvisvoice.live.protocol.RestBodies
import com.lordmuffin.jarvisvoice.live.protocol.SessionMode
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.io.IOException
import java.util.UUID
import java.util.concurrent.TimeUnit

/** A non-2xx REST response. 401/403/404 are [isFatal]: retrying cannot help. */
class LiveHttpException(val status: Int, val body: String) : IOException("HTTP $status: $body") {
    val isFatal: Boolean get() = status == 401 || status == 403 || status == 404
}

/** REST client for Jarvis Live (`server/src/jarvis_live/api/sessions.py`). */
class LiveApiClient(
    baseUrl: String,
    private val deviceToken: String,
    val http: OkHttpClient = defaultClient(),
) {
    val baseUrl: HttpUrl = baseUrl.trim().trimEnd('/').toHttpUrl()

    /** `POST /v1/sessions` → the new session id. */
    suspend fun createSession(mode: SessionMode, channels: List<Channel>, title: String?): String {
        val res = post(url("v1/sessions"), RestBodies.sessionCreate(mode, channels, title))
        val id = res.getString("id")
        UUID.fromString(id)  // schema: format uuid
        return id
    }

    /** `POST /v1/sessions/{id}/ticket` → a single-use 60 s WebSocket ticket. */
    suspend fun ticket(sessionId: String, role: String = "producer"): String =
        post(url("v1/sessions/$sessionId/ticket"), RestBodies.ticket(role)).getString("ticket")

    /** Checks the URL and token: `GET /v1/sessions?limit=1`. */
    suspend fun ping() {
        execute(request(url("v1/sessions").newBuilder().addQueryParameter("limit", "1").build()).get().build())
    }

    /** `wss://…/v1/sessions/{id}/stream?ticket=…` */
    fun streamUrl(sessionId: String, ticket: String): String {
        val url = url("v1/sessions/$sessionId/stream").newBuilder()
            .addQueryParameter("ticket", ticket).build().toString()
        return when {
            url.startsWith("https://") -> "wss://" + url.removePrefix("https://")
            url.startsWith("http://") -> "ws://" + url.removePrefix("http://")
            else -> url
        }
    }

    private fun url(path: String): HttpUrl = baseUrl.newBuilder().addPathSegments(path).build()

    private fun request(url: HttpUrl): Request.Builder =
        Request.Builder().url(url).header("Authorization", "Bearer $deviceToken")

    private suspend fun post(url: HttpUrl, body: JSONObject): JSONObject {
        val req = request(url).post(body.toString().toRequestBody(JSON)).build()
        return JSONObject(execute(req))
    }

    private suspend fun execute(req: Request): String = withContext(Dispatchers.IO) {
        http.newCall(req).execute().use { res ->
            val text = res.body?.string().orEmpty()
            if (!res.isSuccessful) throw LiveHttpException(res.code, text.take(300))
            text
        }
    }

    companion object {
        private val JSON = "application/json".toMediaType()

        fun defaultClient(): OkHttpClient = OkHttpClient.Builder()
            .connectTimeout(15, TimeUnit.SECONDS)
            .readTimeout(30, TimeUnit.SECONDS)
            // Transport-level keepalive; the protocol has no application heartbeat.
            .pingInterval(20, TimeUnit.SECONDS)
            .build()
    }
}
