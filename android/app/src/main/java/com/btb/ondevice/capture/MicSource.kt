package com.btb.ondevice.capture

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import java.util.concurrent.atomic.AtomicBoolean

typealias AudioCallback = (pcm16: ByteArray, sampleRate: Int, timestampS: Double) -> Unit

/**
 * M4: what [MicSource] does with a read that produced no data (0 or negative). Pure and unit-tested
 * without AudioRecord: a run of empty reads first pauses out of the spin, then gives the recorder up.
 */
enum class MicReadPolicy(val sleepMs: Long) {
    /** Fresh failure: keep trying without waiting. */
    RETRY(0L),

    /** Nothing arrived repeatedly: back out of the spin with a short sleep instead of burning CPU. */
    PAUSE(50L),

    /** The recorder is dead (permission revoked etc.): the loop exits and the user is told once. */
    GIVE_UP(0L);

    companion object {
        /** Consecutive empty reads before the loop pauses out of the spin. */
        const val PAUSE_AFTER = 5

        /** Consecutive empty reads before the loop gives up on the recorder. */
        const val GIVE_UP_AFTER = 20

        fun onEmptyRead(consecutive: Int): MicReadPolicy = when {
            consecutive < PAUSE_AFTER -> RETRY
            consecutive < GIVE_UP_AFTER -> PAUSE
            else -> GIVE_UP
        }
    }
}

class MicSource(
    val sampleRate: Int = 16_000,
    val chunkDurationMs: Int = 100, // 100 ms reads
    private val clock: () -> Double = { System.currentTimeMillis() / 1000.0 },
    private val audioCallback: AudioCallback,
    /**
     * M4: fired ONCE when the recorder cannot serve audio while the service keeps monitoring
     * (uninitialised AudioRecord, start failure, or the empty-read ladder reaching give-up, e.g.
     * permission revoked). The service maps this to its existing notification path. One-shot:
     * a broken recorder never spams the user. Null keeps the pure-sensor behaviour (tests, tools).
     */
    private val onMicUnavailable: (() -> Unit)? = null,
) {
    private val isRecording = AtomicBoolean(false)
    private val unavailableNotified = AtomicBoolean(false)
    private var recordThread: Thread? = null
    private var audioRecord: AudioRecord? = null

    val samplesPerChunk = (sampleRate * chunkDurationMs) / 1000
    val bytesPerChunk = samplesPerChunk * 2 // 16-bit PCM = 2 bytes per sample

    @SuppressLint("MissingPermission")
    fun start() {
        if (isRecording.compareAndSet(false, true)) {
            unavailableNotified.set(false)
            val minBufferSize = AudioRecord.getMinBufferSize(
                sampleRate,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT
            )
            val bufferSize = maxOf(minBufferSize, bytesPerChunk * 4)

            try {
                val record = AudioRecord(
                    MediaRecorder.AudioSource.MIC,
                    sampleRate,
                    AudioFormat.CHANNEL_IN_MONO,
                    AudioFormat.ENCODING_PCM_16BIT,
                    bufferSize
                )
                audioRecord = record

                // M4: never read from a half-initialised AudioRecord: permission revoked, audio
                // focus stolen by a call, or a sensor crash. Ask once and stop cleanly.
                if (record.state != AudioRecord.STATE_INITIALIZED) {
                    recorderUnavailable(record)
                    return
                }

                record.startRecording()

                recordThread = Thread({
                    runRecordLoop(record)
                }, "MicSourceThread").apply {
                    priority = Thread.NORM_PRIORITY + 1
                    start()
                }
            } catch (e: Exception) {
                isRecording.set(false)
                audioRecord = null
                notifyMicUnavailableOnce()
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

    /** The recorder read loop. Exits when the service stops OR the recorder stops serving us. */
    private fun runRecordLoop(record: AudioRecord) {
        val buffer = ByteArray(bytesPerChunk)
        var consecutiveEmptyReads = 0

        while (isRecording.get()) {
            var bytesRead = 0
            val startTs = clock()

            while (bytesRead < bytesPerChunk && isRecording.get()) {
                val read = record.read(buffer, bytesRead, bytesPerChunk - bytesRead)
                if (read > 0) {
                    bytesRead += read
                    consecutiveEmptyReads = 0
                } else {
                    // M4: one empty read is usually nothing; a run of them is a recorder that was
                    // revoked, uninitialised or died. Pause out of the spin, then give up ONCE.
                    consecutiveEmptyReads++
                    when (MicReadPolicy.onEmptyRead(consecutiveEmptyReads)) {
                        MicReadPolicy.RETRY -> {}
                        MicReadPolicy.PAUSE -> sleepQuietly(MicReadPolicy.PAUSE.sleepMs)
                        MicReadPolicy.GIVE_UP -> {
                            recorderUnavailable(record)
                            return
                        }
                    }
                    if (!isRecording.get()) return  // service stopped during the pause
                }
            }

            if (bytesRead == bytesPerChunk) {
                audioCallback(buffer.copyOf(), sampleRate, startTs)
            } else if (!isRecording.get()) {
                return
            }
        }
    }

    /** Recorder unusable mid-session: release it, keep the monitoring flag for the service, tell once. */
    private fun recorderUnavailable(record: AudioRecord) {
        try { record.stop() } catch (_: Exception) {}
        try { record.release() } catch (_: Exception) {}
        audioRecord = null
        notifyMicUnavailableOnce()
    }

    private fun notifyMicUnavailableOnce() {
        if (unavailableNotified.compareAndSet(false, true)) {
            onMicUnavailable?.invoke()
        }
    }

    private fun sleepQuietly(ms: Long) {
        try {
            Thread.sleep(ms)
        } catch (_: InterruptedException) {}
    }
}
