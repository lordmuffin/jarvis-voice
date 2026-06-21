package com.lordmuffin.jarvisvoice.chat

import android.content.Context
import android.media.MediaPlayer
import kotlinx.coroutines.suspendCancellableCoroutine
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.util.concurrent.TimeUnit
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

private const val TTS_URL = "http://192.168.1.43:8880/v1/audio/speech"

class TtsRepository(private val context: Context) {

    private val client = OkHttpClient.Builder()
        .readTimeout(30, TimeUnit.SECONDS)
        .connectTimeout(10, TimeUnit.SECONDS)
        .build()

    @Volatile private var player: MediaPlayer? = null

    suspend fun speak(text: String) {
        val body = JSONObject()
            .put("model", "kokoro")
            .put("input", text)
            .put("voice", "af_sky")
            .put("response_format", "mp3")
            .toString()
            .toRequestBody("application/json".toMediaType())

        val response = client.newCall(
            Request.Builder().url(TTS_URL).post(body).build()
        ).execute()
        if (!response.isSuccessful) throw IOException("TTS failed: ${response.code}")

        val bytes = response.body?.bytes() ?: throw IOException("TTS empty body")
        response.close()

        val tmp = File.createTempFile("tts_", ".mp3", context.cacheDir)
        try {
            tmp.writeBytes(bytes)
            playAndAwait(tmp.absolutePath)
        } finally {
            tmp.delete()
        }
    }

    private suspend fun playAndAwait(path: String) = suspendCancellableCoroutine { cont ->
        val mp = MediaPlayer()
        player = mp
        mp.setDataSource(path)
        mp.setOnPreparedListener { it.start() }
        mp.setOnCompletionListener {
            it.release()
            player = null
            if (cont.isActive) cont.resume(Unit)
        }
        mp.setOnErrorListener { _, _, _ ->
            mp.release()
            player = null
            if (cont.isActive) cont.resumeWithException(IOException("MediaPlayer error"))
            true
        }
        cont.invokeOnCancellation {
            mp.release()
            player = null
        }
        mp.prepareAsync()
    }

    fun stop() {
        player?.apply {
            runCatching { if (isPlaying) stop() }
            release()
        }
        player = null
    }
}
