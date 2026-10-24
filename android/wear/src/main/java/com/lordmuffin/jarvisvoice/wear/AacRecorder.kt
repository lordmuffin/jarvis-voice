package com.lordmuffin.jarvisvoice.wear

import android.content.Context
import android.media.MediaRecorder
import android.os.Build
import java.io.File

/**
 * Records mono 16 kHz AAC-LC at 24 kbps (about 180 KB a minute) straight to disk as raw ADTS.
 * ADTS has no trailer to write, so a recording cut short by a crash or dead battery is still a
 * playable file, which a MP4/M4A container is not. The server's ffmpeg reads it (`aac` demuxer).
 */
class AacRecorder(private val context: Context, private val dir: File) {
    private var recorder: MediaRecorder? = null
    var file: File? = null
        private set

    val isRecording get() = recorder != null

    /** [onMaxDuration] runs on the main thread when the safety cap is hit. */
    fun start(id: String, maxDurationMs: Int, onMaxDuration: () -> Unit): File {
        check(recorder == null) { "already recording" }
        dir.mkdirs()
        val out = File(dir, "$id.${com.lordmuffin.jarvisvoice.wearcommon.sync.RecordingCatalog.EXT}")
        val r = if (Build.VERSION.SDK_INT >= 31) MediaRecorder(context) else @Suppress("DEPRECATION") MediaRecorder()
        try {
            r.setAudioSource(MediaRecorder.AudioSource.MIC)
            r.setOutputFormat(MediaRecorder.OutputFormat.AAC_ADTS)
            r.setAudioEncoder(MediaRecorder.AudioEncoder.AAC)
            r.setAudioSamplingRate(SAMPLE_RATE)
            r.setAudioEncodingBitRate(BIT_RATE)
            r.setAudioChannels(1)
            r.setMaxDuration(maxDurationMs)
            r.setOnInfoListener { _, what, _ ->
                if (what == MediaRecorder.MEDIA_RECORDER_INFO_MAX_DURATION_REACHED) onMaxDuration()
            }
            r.setOutputFile(out.absolutePath)
            r.prepare()
            r.start()
        } catch (e: Exception) {
            runCatching { r.release() }
            out.delete()
            throw e
        }
        recorder = r
        file = out
        return out
    }

    /** Stops and returns the file, or null if nothing was recording. A stop error still returns the file: its bytes are on disk. */
    fun stop(): File? {
        val r = recorder ?: return null
        recorder = null
        runCatching { r.stop() }
        runCatching { r.release() }
        return file.also { file = null }
    }

    companion object {
        const val SAMPLE_RATE = 16_000
        const val BIT_RATE = 24_000
    }
}
