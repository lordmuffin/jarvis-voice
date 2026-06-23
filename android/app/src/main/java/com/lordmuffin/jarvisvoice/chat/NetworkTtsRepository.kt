package com.lordmuffin.jarvisvoice.chat

import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import com.lordmuffin.jarvisvoice.DebugLog
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.isActive
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.io.InputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.concurrent.TimeUnit
import kotlin.coroutines.resume

/**
 * TTS via Kokoro (or any OpenAI-compatible /v1/audio/speech endpoint).
 * Returns WAV; plays it via AudioTrack for barge-in-friendly immediate stop().
 */
class NetworkTtsRepository(
    private val baseUrl: String,
    private val voice: String,
) {
    private val http = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .build()

    @Volatile private var track: AudioTrack? = null

    // Speak text using a remote TTS server. Suspends until playback finishes
    // or the coroutine is cancelled (e.g. barge-in calls stop()).
    suspend fun speak(text: String): Unit = withContext(Dispatchers.IO) {
        val body = JSONObject().apply {
            put("model", "kokoro")
            put("input", text)
            put("voice", voice)
            put("response_format", "wav")
        }.toString().toRequestBody("application/json".toMediaType())

        val req = Request.Builder()
            .url("$baseUrl/v1/audio/speech")
            .post(body)
            .build()

        val response = runCatching { http.newCall(req).execute() }.getOrElse { e ->
            DebugLog.e("TTS/Network", "HTTP failed: ${e.message}")
            return@withContext
        }
        if (!response.isSuccessful) {
            DebugLog.e("TTS/Network", "Non-2xx ${response.code}")
            response.close()
            return@withContext
        }

        val bytes = response.body?.bytes() ?: run { response.close(); return@withContext }
        response.close()

        val (sampleRate, channelConfig, audioFormat, pcmOffset) = parseWavHeader(bytes) ?: run {
            DebugLog.e("TTS/Network", "Invalid WAV header")
            return@withContext
        }

        val minBuf = AudioTrack.getMinBufferSize(sampleRate, channelConfig, audioFormat)
        val at = AudioTrack.Builder()
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_ASSISTANT)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build()
            )
            .setAudioFormat(
                AudioFormat.Builder()
                    .setSampleRate(sampleRate)
                    .setChannelMask(channelConfig)
                    .setEncoding(audioFormat)
                    .build()
            )
            .setBufferSizeInBytes(maxOf(minBuf, 8192))
            .setTransferMode(AudioTrack.MODE_STREAM)
            .build()

        track = at
        at.play()

        suspendCancellableCoroutine { cont ->
            cont.invokeOnCancellation {
                at.stop()
                at.release()
                track = null
            }

            val pcm = bytes.copyOfRange(pcmOffset, bytes.size)
            val chunkSize = 4096
            var offset = 0
            while (offset < pcm.size) {
                if (!cont.isActive) break
                val end = minOf(offset + chunkSize, pcm.size)
                val written = at.write(pcm, offset, end - offset)
                if (written < 0) break
                offset += written
            }

            if (cont.isActive) {
                at.stop()
                at.release()
                track = null
                cont.resume(Unit)
            }
        }
    }

    fun stop() {
        runCatching { track?.stop(); track?.release() }
        track = null
    }

    fun destroy() = stop()

    // ── WAV header parser ─────────────────────────────────────────────────────

    private data class WavParams(
        val sampleRate: Int,
        val channelConfig: Int,
        val audioFormat: Int,
        val pcmOffset: Int,
    )

    private fun parseWavHeader(data: ByteArray): WavParams? {
        if (data.size < 44) return null
        val buf = ByteBuffer.wrap(data).order(ByteOrder.LITTLE_ENDIAN)
        // RIFF check
        if (buf.get() != 'R'.code.toByte() || buf.get() != 'I'.code.toByte() ||
            buf.get() != 'F'.code.toByte() || buf.get() != 'F'.code.toByte()) return null
        buf.int // chunk size
        if (buf.get() != 'W'.code.toByte() || buf.get() != 'A'.code.toByte() ||
            buf.get() != 'V'.code.toByte() || buf.get() != 'E'.code.toByte()) return null
        // fmt chunk
        if (buf.get() != 'f'.code.toByte() || buf.get() != 'm'.code.toByte() ||
            buf.get() != 't'.code.toByte() || buf.get() != ' '.code.toByte()) return null
        val fmtSize = buf.int
        val audioFmt = buf.short.toInt()  // 1 = PCM
        if (audioFmt != 1) return null
        val channels = buf.short.toInt()
        val sampleRate = buf.int
        buf.int  // byte rate
        buf.short // block align
        val bitsPerSample = buf.short.toInt()
        if (fmtSize > 16) buf.position(buf.position() + (fmtSize - 16))

        // scan for data chunk
        var pos = buf.position()
        while (pos + 8 <= data.size) {
            val id = String(data, pos, 4, Charsets.US_ASCII)
            val chunkLen = ByteBuffer.wrap(data, pos + 4, 4).order(ByteOrder.LITTLE_ENDIAN).int
            if (id == "data") {
                val pcmOffset = pos + 8
                val channelConfig = if (channels == 1) AudioFormat.CHANNEL_OUT_MONO
                                    else AudioFormat.CHANNEL_OUT_STEREO
                val afmt = if (bitsPerSample == 16) AudioFormat.ENCODING_PCM_16BIT
                           else AudioFormat.ENCODING_PCM_8BIT
                return WavParams(sampleRate, channelConfig, afmt, pcmOffset)
            }
            pos += 8 + chunkLen
        }
        return null
    }
}
