package com.btb.ondevice.audio

import java.nio.ByteBuffer
import java.nio.ByteOrder

/**
 * In-memory rolling circular buffer of 16-bit mono PCM audio for snippet generation.
 * Default: 5.0 seconds at 16,000 Hz = 80,000 samples = 160,000 bytes.
 * Thread-safe for concurrent mic writing and event snapshotting.
 */
class AudioSnippetBuffer(
    val sampleRate: Int = 16_000,
    val durationSeconds: Double = 5.0,
) {
    val capacitySamples: Int = (sampleRate * durationSeconds).toInt()
    private val buffer: ShortArray = ShortArray(capacitySamples)
    private var writeHead: Int = 0
    private var totalSamplesWritten: Long = 0L
    private val lock = Any()

    fun reset() {
        synchronized(lock) {
            writeHead = 0
            totalSamplesWritten = 0L
            buffer.fill(0)
        }
    }

    /**
     * Push incoming 16-bit mono PCM samples into the rolling ring buffer.
     */
    fun push(samples: ShortArray) {
        if (samples.isEmpty()) return
        synchronized(lock) {
            for (i in samples.indices) {
                buffer[writeHead] = samples[i]
                writeHead = (writeHead + 1) % capacitySamples
                totalSamplesWritten++
            }
        }
    }

    /**
     * Push raw PCM16 bytes into the rolling ring buffer (convenience method).
     */
    fun push(pcm16Bytes: ByteArray) {
        if (pcm16Bytes.size < 2) return
        val count = pcm16Bytes.size / 2
        val shorts = ShortArray(count)
        ByteBuffer.wrap(pcm16Bytes).order(ByteOrder.LITTLE_ENDIAN).asShortBuffer().get(shorts)
        push(shorts)
    }

    /**
     * Snapshot the most recent [requestedSeconds] (clamped to max capacity) as a valid 16-bit mono PCM WAV file.
     */
    fun captureWavSnippet(requestedSeconds: Double = durationSeconds): ByteArray {
        synchronized(lock) {
            val samplesToRead = minOf(
                (sampleRate * requestedSeconds).toInt(),
                capacitySamples,
                totalSamplesWritten.toInt()
            ).coerceAtLeast(0)

            val pcmData = ShortArray(samplesToRead)
            if (samplesToRead > 0) {
                var readHead = (writeHead - samplesToRead + capacitySamples) % capacitySamples
                for (i in 0 until samplesToRead) {
                    pcmData[i] = buffer[readHead]
                    readHead = (readHead + 1) % capacitySamples
                }
            }

            return encodeWav(pcmData, sampleRate)
        }
    }

    companion object {
        /**
         * Helper to encode 16-bit mono PCM samples into a standard 44-byte RIFF/WAVE container.
         */
        fun encodeWav(samples: ShortArray, sampleRate: Int = 16_000): ByteArray {
            val numChannels = 1
            val bitsPerSample = 16
            val byteRate = sampleRate * numChannels * (bitsPerSample / 8)
            val blockAlign = numChannels * (bitsPerSample / 8)
            val pcmBytesLength = samples.size * 2
            val totalDataLength = pcmBytesLength + 36

            val header = ByteBuffer.allocate(44).order(ByteOrder.LITTLE_ENDIAN).apply {
                // ChunkID "RIFF" (Big Endian ASCII)
                put('R'.code.toByte())
                put('I'.code.toByte())
                put('F'.code.toByte())
                put('F'.code.toByte())
                putInt(totalDataLength) // ChunkSize
                // Format "WAVE" (Big Endian ASCII)
                put('W'.code.toByte())
                put('A'.code.toByte())
                put('V'.code.toByte())
                put('E'.code.toByte())
                // Subchunk1ID "fmt " (Big Endian ASCII)
                put('f'.code.toByte())
                put('m'.code.toByte())
                put('t'.code.toByte())
                put(' '.code.toByte())
                putInt(16) // Subchunk1Size (16 for PCM)
                putShort(1.toShort()) // AudioFormat (1 = PCM)
                putShort(numChannels.toShort())
                putInt(sampleRate)
                putInt(byteRate)
                putShort(blockAlign.toShort())
                putShort(bitsPerSample.toShort())
                // Subchunk2ID "data" (Big Endian ASCII)
                put('d'.code.toByte())
                put('a'.code.toByte())
                put('t'.code.toByte())
                put('a'.code.toByte())
                putInt(pcmBytesLength) // Subchunk2Size
            }.array()

            val pcmBytes = ByteArray(pcmBytesLength)
            ByteBuffer.wrap(pcmBytes).order(ByteOrder.LITTLE_ENDIAN).asShortBuffer().put(samples)

            val fullWav = ByteArray(44 + pcmBytesLength)
            System.arraycopy(header, 0, fullWav, 0, 44)
            System.arraycopy(pcmBytes, 0, fullWav, 44, pcmBytesLength)
            return fullWav
        }
    }
}
