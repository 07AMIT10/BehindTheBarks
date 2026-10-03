package com.btb.ondevice.capture

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import java.util.concurrent.atomic.AtomicBoolean

typealias AudioCallback = (pcm16: ByteArray, sampleRate: Int, timestampS: Double) -> Unit

class MicSource(
    val sampleRate: Int = 16_000,
    val chunkDurationMs: Int = 100, // 100 ms reads
    private val clock: () -> Double = { System.currentTimeMillis() / 1000.0 },
    private val audioCallback: AudioCallback,
) {
    private val isRecording = AtomicBoolean(false)
    private var recordThread: Thread? = null
    private var audioRecord: AudioRecord? = null

    val samplesPerChunk = (sampleRate * chunkDurationMs) / 1000
    val bytesPerChunk = samplesPerChunk * 2 // 16-bit PCM = 2 bytes per sample

    @SuppressLint("MissingPermission")
    fun start() {
        if (isRecording.compareAndSet(false, true)) {
            val minBufferSize = AudioRecord.getMinBufferSize(
                sampleRate,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT
            )
            val bufferSize = maxOf(minBufferSize, bytesPerChunk * 4)

            try {
                audioRecord = AudioRecord(
                    MediaRecorder.AudioSource.MIC,
                    sampleRate,
                    AudioFormat.CHANNEL_IN_MONO,
                    AudioFormat.ENCODING_PCM_16BIT,
                    bufferSize
                )

                audioRecord?.startRecording()

                recordThread = Thread({
                    val buffer = ByteArray(bytesPerChunk)
                    while (isRecording.get()) {
                        val record = audioRecord ?: break
                        var bytesRead = 0
                        val startTs = clock()

                        while (bytesRead < bytesPerChunk && isRecording.get()) {
                            val read = record.read(buffer, bytesRead, bytesPerChunk - bytesRead)
                            if (read > 0) {
                                bytesRead += read
                            } else if (read < 0) {
                                break
                            }
                        }

                        if (bytesRead == bytesPerChunk) {
                            audioCallback(buffer.copyOf(), sampleRate, startTs)
                        }
                    }
                }, "MicSourceThread").apply {
                    priority = Thread.NORM_PRIORITY + 1
                    start()
                }
            } catch (e: Exception) {
                isRecording.set(false)
            }
        }
    }

    fun stop() {
        if (isRecording.compareAndSet(true, false)) {
            try {
                audioRecord?.stop()
                audioRecord?.release()
            } catch (_: Exception) {}
            audioRecord = null
            recordThread?.join(1000)
            recordThread = null
        }
    }
}
