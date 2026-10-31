package com.lordmuffin.jarvisvoice.live

import android.annotation.SuppressLint
import android.content.Context
import android.media.AudioDeviceInfo
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioRecordingConfiguration
import android.media.AudioRecord
import android.media.MediaRecorder
import android.media.audiofx.AcousticEchoCanceler
import android.media.audiofx.NoiseSuppressor
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
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
class AudioCapture(context: Context, private val builtInMicOnly: Boolean = false) {

    /** Called on the capture thread for every frame: raw samples, PCM16LE bytes, and `t_ms`. */
    fun interface FrameSink {
        fun onFrame(samples: ShortArray, pcm: ByteArray, tMs: Long)
    }

    private val appContext = context.applicationContext
    private val deviceRouter = AudioDeviceRouter(context)
    private var audioRecord: AudioRecord? = null
    private var aec: AcousticEchoCanceler? = null
    private var noiseSuppressor: NoiseSuppressor? = null
    private var recordingCallback: Any? = null
    private var thread: Thread? = null
    private var scoStarted = false
    private val silence = SilenceDetector()
    private val recovery = RecoveryPolicy()
    private var onInterruption: ((Boolean) -> Unit)? = null
    @Volatile private var running = false
    @Volatile private var muted = false
    @Volatile private var recovering = false
    @Volatile private var clientSilenced = false
    @Volatile private var interrupted = false

    /**
     * Muted = the microphone is released (the system mic indicator goes off) and frames of silence
     * keep the timeline and sequence numbers going. Unmuting re-opens it.
     */
    fun setMuted(value: Boolean) {
        muted = value
    }

    /** Level of the last frame, 0..1, for the UI meter. */
    @Volatile var level: Float = 0f
        private set

    /**
     * @param onInterruption true when the mic has been taken away (a call, or the device died and is
     * being re-opened), false when audio is back. The session keeps running; only [onError] ends it.
     */
    @SuppressLint("MissingPermission")  // the service checks RECORD_AUDIO before starting
    fun start(sink: FrameSink, onError: (Throwable) -> Unit, onInterruption: (Boolean) -> Unit = {}) {
        check(!running) { "already running" }
        this.onInterruption = onInterruption
        val preferred = if (builtInMicOnly) deviceRouter.builtInMic() else deviceRouter.getPreferredDevice()
        if (preferred != null && deviceRouter.isBluetoothSco(preferred)) {
            deviceRouter.startBluetoothSco()
            scoStarted = deviceRouter.waitForSco(2_000L)
            DebugLog.i(TAG, "Bluetooth SCO ready=$scoStarted")
        }
        val record = try {
            openRecord(preferred)
        } catch (e: Exception) {
            release()
            throw e
        }
        running = true

        thread = Thread({
            var frameIndex = 0L
            var gapMs = 0L  // time lost while the mic was being re-opened, so `t_ms` stays real
            var rec: AudioRecord? = record
            val samples = ShortArray(FRAME_SAMPLES)
            val pacer = FramePacer(AudioFrame.FRAME_MS.toLong())
            try {
                while (running) {
                    if (muted) {
                        if (rec != null) {
                            releaseRecord()
                            rec = null
                            silence.reset()
                            publishInterrupted()  // a call that silenced us no longer matters
                        }
                        val wait = pacer.sleepMs(SystemClock.elapsedRealtime())
                        if (wait > 0) Thread.sleep(wait)
                        if (!running) break
                        level = 0f
                        val quiet = ShortArray(FRAME_SAMPLES)
                        sink.onFrame(quiet, toLittleEndian(quiet), frameIndex * AudioFrame.FRAME_MS + gapMs)
                        frameIndex++
                        continue
                    }
                    pacer.reset()
                    if (rec == null) {  // just unmuted
                        val started = SystemClock.elapsedRealtime()
                        rec = try {
                            openRecord(preferred)
                        } catch (e: Exception) {
                            DebugLog.w(TAG, "unmute: first open failed (${e.message}), retrying")
                            reopen(preferred) ?: throw IllegalStateException("microphone lost")
                        }
                        gapMs += SystemClock.elapsedRealtime() - started
                        continue
                    }
                    val current = rec ?: continue
                    // Fill a whole 100 ms frame; AudioRecord may return short reads.
                    var filled = 0
                    var failed = false
                    while (filled < FRAME_SAMPLES && running) {
                        val n = current.read(samples, filled, FRAME_SAMPLES - filled)
                        if (n < 0) { failed = true; break }
                        filled += n
                    }
                    if (failed) {
                        DebugLog.w(TAG, "AudioRecord.read failed; re-opening the microphone")
                        val started = SystemClock.elapsedRealtime()
                        rec = reopen(preferred) ?: throw IllegalStateException("microphone lost")
                        gapMs += SystemClock.elapsedRealtime() - started
                        continue
                    }
                    if (filled < FRAME_SAMPLES) break
                    val frame = samples.copyOf()
                    level = (VadSegmenter.rms(frame) / 6000.0).coerceIn(0.0, 1.0).toFloat()
                    if (silence.onFrame(frame)) publishInterrupted()
                    sink.onFrame(frame, toLittleEndian(frame), frameIndex * AudioFrame.FRAME_MS + gapMs)
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

    @SuppressLint("MissingPermission")
    private fun openRecord(preferred: AudioDeviceInfo?): AudioRecord {
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
            throw IllegalStateException("microphone unavailable")
        }
        if (preferred != null) record.preferredDevice = preferred
        if (AcousticEchoCanceler.isAvailable()) aec = AcousticEchoCanceler.create(record.audioSessionId)
        if (NoiseSuppressor.isAvailable()) noiseSuppressor = NoiseSuppressor.create(record.audioSessionId)
        audioRecord = record
        record.startRecording()
        silence.reset()
        watchForSilencing(record)
        return record
    }

    /** Retries [openRecord] on the [RecoveryPolicy] schedule; null if the mic never came back. */
    private fun reopen(preferred: AudioDeviceInfo?): AudioRecord? {
        releaseRecord()
        recovering = true
        publishInterrupted()
        val failingSince = SystemClock.elapsedRealtime()
        var attempt = 0
        while (running) {
            val wait = recovery.delayMs(attempt, SystemClock.elapsedRealtime() - failingSince) ?: return null
            val until = SystemClock.elapsedRealtime() + wait
            while (running && SystemClock.elapsedRealtime() < until) Thread.sleep(50)
            if (!running) return null
            try {
                val record = openRecord(preferred)
                recovering = false
                publishInterrupted()
                DebugLog.i(TAG, "microphone re-opened after ${SystemClock.elapsedRealtime() - failingSince} ms")
                return record
            } catch (e: Exception) {
                DebugLog.w(TAG, "re-open attempt ${attempt + 1} failed: ${e.message}")
                releaseRecord()
            }
            attempt++
        }
        return null
    }

    /** Android 10+ also says so directly when another app's capture silences ours. */
    private fun watchForSilencing(record: AudioRecord) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) return
        val callback = object : AudioManager.AudioRecordingCallback() {
            override fun onRecordingConfigChanged(configs: MutableList<AudioRecordingConfiguration>) {
                val mine = configs.firstOrNull { it.clientAudioSessionId == record.audioSessionId } ?: return
                if (clientSilenced != mine.isClientSilenced) {
                    clientSilenced = mine.isClientSilenced
                    publishInterrupted()
                }
            }
        }
        recordingCallback = callback
        appContext.getSystemService(AudioManager::class.java)
            .registerAudioRecordingCallback(callback, Handler(Looper.getMainLooper()))
    }

    private fun publishInterrupted() {
        val now = recovering || clientSilenced || silence.silent
        if (now == interrupted) return
        interrupted = now
        DebugLog.i(TAG, "interrupted=$now (recovering=$recovering silenced=$clientSilenced zeros=${silence.silent})")
        onInterruption?.invoke(now)
    }

    private fun releaseRecord() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            (recordingCallback as? AudioManager.AudioRecordingCallback)?.let {
                runCatching { appContext.getSystemService(AudioManager::class.java).unregisterAudioRecordingCallback(it) }
            }
        }
        recordingCallback = null
        clientSilenced = false
        aec?.release(); aec = null
        noiseSuppressor?.release(); noiseSuppressor = null
        runCatching { audioRecord?.stop(); audioRecord?.release() }
        audioRecord = null
    }

    private fun release() {
        releaseRecord()
        if (scoStarted) { deviceRouter.stopBluetoothSco(); scoStarted = false }
        level = 0f
        recovering = false
        silence.reset()
        interrupted = false
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
