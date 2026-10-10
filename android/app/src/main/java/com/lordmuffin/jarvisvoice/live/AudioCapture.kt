package com.lordmuffin.jarvisvoice.live

import android.annotation.SuppressLint
import android.content.Context
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.media.audiofx.AcousticEchoCanceler
import android.media.audiofx.NoiseSuppressor
import com.lordmuffin.jarvisvoice.AudioDeviceRouter
import com.lordmuffin.jarvisvoice.DebugLog
import com.lordmuffin.jarvisvoice.live.protocol.AudioFrame
import java.nio.ByteBuffer
import java.nio.ByteOrder

/**
 * Microphone capture for a Live session: 16 kHz mono PCM16 in 100 ms frames, which is exactly the
 * wire format, so no resampling is needed. Honors the preferred input device from Settings
 * (including Bluetooth SCO) and attaches AEC/NS like the dictation engine does.
 */
class AudioCapture(context: Context) {

    /** Called on the capture thread for every frame: raw samples, PCM16LE bytes, and `t_ms`. */
    fun interface FrameSink {
        fun onFrame(samples: ShortArray, pcm: ByteArray, tMs: Long)
    }

    private val deviceRouter = AudioDeviceRouter(context)
    private var audioRecord: AudioRecord? = null
    private var aec: AcousticEchoCanceler? = null
    private var noiseSuppressor: NoiseSuppressor? = null
    private var thread: Thread? = null
    private var scoStarted = false
    @Volatile private var running = false

    /** Level of the last frame, 0..1, for the UI meter. */
    @Volatile var level: Float = 0f
        private set

    @SuppressLint("MissingPermission")  // the service checks RECORD_AUDIO before starting
    fun start(sink: FrameSink, onError: (Throwable) -> Unit) {
        check(!running) { "already running" }
        val preferred = deviceRouter.getPreferredDevice()
        if (preferred != null && deviceRouter.isBluetoothSco(preferred)) {
            deviceRouter.startBluetoothSco()
            scoStarted = deviceRouter.waitForSco(2_000L)
            DebugLog.i(TAG, "Bluetooth SCO ready=$scoStarted")
        }

        val minBuf = AudioRecord.getMinBufferSize(
            AudioFrame.SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT
        )
        val record = AudioRecord(
            MediaRecorder.AudioSource.VOICE_RECOGNITION,
            AudioFrame.SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
            maxOf(minBuf, FRAME_SAMPLES * 2 * 8),
        )
        if (record.state != AudioRecord.STATE_INITIALIZED) {
            record.release()
            release()
            throw IllegalStateException("microphone unavailable")
        }
        if (preferred != null) record.preferredDevice = preferred
        if (AcousticEchoCanceler.isAvailable()) aec = AcousticEchoCanceler.create(record.audioSessionId)
        if (NoiseSuppressor.isAvailable()) noiseSuppressor = NoiseSuppressor.create(record.audioSessionId)
        audioRecord = record
        record.startRecording()
        running = true

        thread = Thread({
            var frameIndex = 0L
            val samples = ShortArray(FRAME_SAMPLES)
            try {
                while (running) {
                    // Fill a whole 100 ms frame; AudioRecord may return short reads.
                    var filled = 0
                    while (filled < FRAME_SAMPLES && running) {
                        val n = record.read(samples, filled, FRAME_SAMPLES - filled)
                        if (n < 0) throw IllegalStateException("AudioRecord.read error $n")
                        filled += n
                    }
                    if (filled < FRAME_SAMPLES) break
                    val frame = samples.copyOf()
                    level = (VadSegmenter.rms(frame) / 6000.0).coerceIn(0.0, 1.0).toFloat()
                    sink.onFrame(frame, toLittleEndian(frame), frameIndex * AudioFrame.FRAME_MS)
                    frameIndex++
                }
            } catch (t: Throwable) {
                DebugLog.e(TAG, "capture loop failed", t)
                if (running) onError(t)
            }
        }, "jarvis-live-capture").also { it.start() }
        DebugLog.i(TAG, "capture started device=${preferred?.let { deviceRouter.deviceLabel(it) } ?: "default"}")
    }

    /** Stops capture and waits for the capture thread to deliver its last frame. */
    fun stop() {
        running = false
        thread?.join(1_000)
        thread = null
        release()
    }

    private fun release() {
        aec?.release(); aec = null
        noiseSuppressor?.release(); noiseSuppressor = null
        runCatching { audioRecord?.stop(); audioRecord?.release() }
        audioRecord = null
        if (scoStarted) { deviceRouter.stopBluetoothSco(); scoStarted = false }
        level = 0f
    }

    companion object {
        private const val TAG = "LiveCapture"
        const val FRAME_SAMPLES = AudioFrame.SAMPLE_RATE * AudioFrame.FRAME_MS / 1000  // 1600

        fun toLittleEndian(samples: ShortArray): ByteArray {
            val buf = ByteBuffer.allocate(samples.size * 2).order(ByteOrder.LITTLE_ENDIAN)
            buf.asShortBuffer().put(samples)
            return buf.array()
        }
    }
}
