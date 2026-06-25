package com.lordmuffin.jarvisvoice.wear

import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import java.io.File
import java.io.FileOutputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder

private const val SAMPLE_RATE = 16000
private const val CHANNEL_CONFIG = AudioFormat.CHANNEL_IN_MONO
private const val AUDIO_FORMAT = AudioFormat.ENCODING_PCM_16BIT

class AudioRecorder(private val filesDir: File) {

    private var audioRecord: AudioRecord? = null
    private var isRecording = false
    private var currentFile: File? = null

    val isActive get() = isRecording

    fun startRecording() {
        if (isRecording) return
        val bufferSize = AudioRecord.getMinBufferSize(SAMPLE_RATE, CHANNEL_CONFIG, AUDIO_FORMAT)
        val record = AudioRecord(
            MediaRecorder.AudioSource.MIC,
            SAMPLE_RATE,
            CHANNEL_CONFIG,
            AUDIO_FORMAT,
            bufferSize * 4,
        )
        val outFile = File(filesDir, "rec_${System.currentTimeMillis()}.wav")
        currentFile = outFile
        audioRecord = record
        isRecording = true
        record.startRecording()

        Thread {
            val pcmBytes = mutableListOf<Byte>()
            val buffer = ShortArray(bufferSize)
            while (isRecording) {
                val read = record.read(buffer, 0, buffer.size)
                if (read > 0) {
                    val byteBuffer = ByteBuffer.allocate(read * 2).order(ByteOrder.LITTLE_ENDIAN)
                    for (i in 0 until read) byteBuffer.putShort(buffer[i])
                    pcmBytes.addAll(byteBuffer.array().toList())
                }
            }
            writeWav(outFile, pcmBytes.toByteArray())
        }.start()
    }

    /** Stops recording and returns the WAV file, or null if nothing was recorded. */
    fun stopRecording(): File? {
        if (!isRecording) return null
        isRecording = false
        audioRecord?.apply { stop(); release() }
        audioRecord = null
        // give the recording thread a moment to finish writing
        Thread.sleep(200)
        return currentFile.also { currentFile = null }
    }

    private fun writeWav(file: File, pcm: ByteArray) {
        val totalDataLen = pcm.size + 36
        val byteRate = SAMPLE_RATE * 2
        FileOutputStream(file).use { out ->
            fun int32LE(v: Int) = byteArrayOf(
                (v and 0xff).toByte(), (v shr 8 and 0xff).toByte(),
                (v shr 16 and 0xff).toByte(), (v shr 24 and 0xff).toByte()
            )
            fun int16LE(v: Int) = byteArrayOf((v and 0xff).toByte(), (v shr 8 and 0xff).toByte())

            out.write("RIFF".toByteArray())
            out.write(int32LE(totalDataLen))
            out.write("WAVE".toByteArray())
            out.write("fmt ".toByteArray())
            out.write(int32LE(16))
            out.write(int16LE(1))           // PCM
            out.write(int16LE(1))           // mono
            out.write(int32LE(SAMPLE_RATE))
            out.write(int32LE(byteRate))
            out.write(int16LE(2))           // block align
            out.write(int16LE(16))          // bits per sample
            out.write("data".toByteArray())
            out.write(int32LE(pcm.size))
            out.write(pcm)
        }
    }
}
