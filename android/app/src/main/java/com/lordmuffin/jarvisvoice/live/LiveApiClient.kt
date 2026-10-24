package com.lordmuffin.jarvisvoice.live

import com.lordmuffin.jarvisvoice.live.protocol.Channel
import com.lordmuffin.jarvisvoice.live.protocol.RestBodies
import com.lordmuffin.jarvisvoice.live.protocol.SessionMode
import com.lordmuffin.jarvisvoice.wearcommon.RecordingUploader
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withContext
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONException
import org.json.JSONObject
import java.io.File
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

    /**
     * Uploads a recorded file (`POST /v1/sessions/upload`) with this client's server and token. It has
     * its own timeouts: the server decodes the audio before it answers.
     */
    fun recordingUploader(): RecordingUploader = RecordingUploader(baseUrl.toString(), deviceToken)

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

    /**
     * `GET /healthz` within [PROBE_TIMEOUT_S]: can this network reach the server at all? Any
     * failure is false. Checks the body so a captive portal answering 200 doesn't count.
     */
    suspend fun isReachable(): Boolean {
        val body = health() ?: return false
        return try {
            JSONObject(body).optString("status") == "ok"
        } catch (e: JSONException) {
            false
        }
    }

    /**
     * The server's release channel, from the `environment` in `GET /healthz` (unauthenticated).
     * Null when it can't be reached within [PROBE_TIMEOUT_S] or doesn't say.
     */
    suspend fun channel(): ServerChannel? = health()?.let(ServerChannel::fromHealthz)

    /** The body of a 2xx `GET /healthz` within [PROBE_TIMEOUT_S], or null. */
    private suspend fun health(): String? = withContext(Dispatchers.IO) {
        val client = http.newBuilder().callTimeout(PROBE_TIMEOUT_S, TimeUnit.SECONDS).build()
        try {
            client.newCall(Request.Builder().url(url("healthz")).get().build()).execute().use { res ->
                if (res.isSuccessful) res.body?.string().orEmpty() else null
            }
        } catch (e: IOException) {
            null
        }
    }

    /** `GET /v1/me`: who the device token belongs to. */
    suspend fun me(): RemoteUser = RemoteSessions.parseMe(execute(request(url("v1/me")).get().build()))

    /** `GET /v1/sessions?limit=&offset=`: the caller's own sessions, newest first. */
    suspend fun listSessions(limit: Int = 20, offset: Int = 0): SessionPage {
        val url = url("v1/sessions").newBuilder()
            .addQueryParameter("limit", limit.toString())
            .addQueryParameter("offset", offset.toString())
            .build()
        return RemoteSessions.parsePage(execute(request(url).get().build()))
    }

    /** `GET /v1/sessions/{id}`: segments, copilot, markers, the final note and audio availability. */
    suspend fun getSession(id: String): RemoteSessionDetail =
        RemoteSessions.parseDetail(execute(request(sessionUrl(id)).get().build()))

    /**
     * `POST /v1/sessions/{id}/process`: transcribe the stored recording again, then rewrite the note.
     * Never starts a second job; the result then carries the one already running.
     */
    suspend fun process(id: String): ProcessResult {
        val url = sessionUrl(id).newBuilder().addPathSegment("process").build()
        return RemoteSessions.parseProcessResult(execute(request(url).post(EMPTY_BODY).build()))
    }

    /** `GET /v1/sessions/{id}/processing`: just the job state, for polling while one runs. */
    suspend fun getProcessing(id: String): RemoteProcessing {
        val url = sessionUrl(id).newBuilder().addPathSegment("processing").build()
        return RemoteSessions.parseProcessing(execute(request(url).get().build()))
    }

    /** `PATCH /v1/sessions/{id}` `{title}`; a blank title clears it. */
    suspend fun renameSession(id: String, title: String?) {
        val body = JSONObject().put("title", title?.trim()?.takeIf { it.isNotEmpty() } ?: JSONObject.NULL)
        execute(request(sessionUrl(id)).patch(body.toString().toRequestBody(JSON)).build())
    }

    /**
     * `GET /v1/sessions/{id}/audio/{mic|system}.wav` into [dest] (written to a temp file first, so
     * a failed download never leaves a truncated WAV behind). 404 when there is no recording.
     * [onProgress] gets (bytes so far, total or -1) on the IO thread.
     */
    suspend fun downloadAudio(
        id: String,
        channel: String,
        dest: File,
        onProgress: ((Long, Long) -> Unit)? = null,
    ): File = withContext(Dispatchers.IO) {
        val url = sessionUrl(id).newBuilder().addPathSegment("audio").addPathSegment("$channel.wav").build()
        val req = request(url).get().build()
        // Long recordings stream for a while; don't let the 30 s read timeout cut them off.
        val client = http.newBuilder().readTimeout(2, TimeUnit.MINUTES).build()
        client.newCall(req).execute().use { res ->
            if (!res.isSuccessful) throw LiveHttpException(res.code, res.body?.string().orEmpty().take(300))
            val body = res.body ?: throw IOException("empty response")
            val total = body.contentLength()
            dest.parentFile?.mkdirs()
            val tmp = File(dest.path + ".part")
            try {
                body.byteStream().use { input ->
                    tmp.outputStream().use { out ->
                        val buf = ByteArray(64 * 1024)
                        var done = 0L
                        while (true) {
                            val n = input.read(buf)
                            if (n < 0) break
                            ensureActive()
                            out.write(buf, 0, n)
                            done += n
                            onProgress?.invoke(done, total)
                        }
                    }
                }
                if (dest.exists()) dest.delete()
                if (!tmp.renameTo(dest)) throw IOException("could not save ${dest.name}")
            } finally {
                tmp.delete()
            }
            dest
        }
    }

    /**
     * `POST /v1/auth/device-code` `{code, verifier, device_name}` → a new device token for the
     * signed-in user. Unauthenticated; 400 when the code is invalid, expired or already used.
     */
    suspend fun redeemDeviceCode(code: String, verifier: String, deviceName: String): DeviceToken {
        val body = JSONObject()
            .put("code", code)
            .put("verifier", verifier)
            .put("device_name", deviceName)
        val req = Request.Builder().url(url("v1/auth/device-code"))
            .post(body.toString().toRequestBody(JSON))
            .build()
        return RemoteSessions.parseDeviceToken(execute(req))
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

    /** `v1/sessions/{id}`, with the id encoded as a single path segment. */
    private fun sessionUrl(id: String): HttpUrl = url("v1/sessions").newBuilder().addPathSegment(id).build()

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
        private val EMPTY_BODY = ByteArray(0).toRequestBody(null)

        /** Away, the server's address usually times out rather than refusing; don't wait long. */
        const val PROBE_TIMEOUT_S = 4L

        fun defaultClient(): OkHttpClient = OkHttpClient.Builder()
            .connectTimeout(15, TimeUnit.SECONDS)
            .readTimeout(30, TimeUnit.SECONDS)
            // Transport-level keepalive; the protocol has no application heartbeat.
            .pingInterval(20, TimeUnit.SECONDS)
            .build()
    }
}
