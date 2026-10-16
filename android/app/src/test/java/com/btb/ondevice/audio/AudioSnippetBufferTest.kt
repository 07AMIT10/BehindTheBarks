package com.btb.ondevice.audio

import com.google.common.truth.Truth.assertThat
import org.junit.jupiter.api.Test
import java.nio.ByteBuffer
import java.nio.ByteOrder

class AudioSnippetBufferTest {

    @Test
    fun `empty buffer produces valid WAV header with zero data`() {
        val buffer = AudioSnippetBuffer(sampleRate = 16_000, durationSeconds = 1.0)
        val wav = buffer.captureWavSnippet(1.0)

        assertThat(wav.size).isEqualTo(44)
        val riff = String(wav, 0, 4)
        val wave = String(wav, 8, 4)
        val fmt = String(wav, 12, 4)
        val data = String(wav, 36, 4)

        assertThat(riff).isEqualTo("RIFF")
        assertThat(wave).isEqualTo("WAVE")
        assertThat(fmt).isEqualTo("fmt ")
        assertThat(data).isEqualTo("data")

        val subchunk2Size = ByteBuffer.wrap(wav, 40, 4).order(ByteOrder.LITTLE_ENDIAN).int
        assertThat(subchunk2Size).isEqualTo(0)
    }

    @Test
    fun `buffer stores samples and produces valid WAV structure`() {
        val buffer = AudioSnippetBuffer(sampleRate = 16_000, durationSeconds = 2.0)
        val samples = ShortArray(16_000) { i -> (i % 1000).toShort() }
        buffer.push(samples)

        val wav = buffer.captureWavSnippet(1.0)
        assertThat(wav.size).isEqualTo(44 + 32_000)

        val sampleRate = ByteBuffer.wrap(wav, 24, 4).order(ByteOrder.LITTLE_ENDIAN).int
        val bitsPerSample = ByteBuffer.wrap(wav, 34, 2).order(ByteOrder.LITTLE_ENDIAN).short
        val subchunk2Size = ByteBuffer.wrap(wav, 40, 4).order(ByteOrder.LITTLE_ENDIAN).int

        assertThat(sampleRate).isEqualTo(16_000)
        assertThat(bitsPerSample.toInt()).isEqualTo(16)
        assertThat(subchunk2Size).isEqualTo(32_000)

        // Read back first sample
        val firstSample = ByteBuffer.wrap(wav, 44, 2).order(ByteOrder.LITTLE_ENDIAN).short
        assertThat(firstSample.toInt()).isEqualTo(0)
    }

    @Test
    fun `buffer wraps around circular capacity properly`() {
        val buffer = AudioSnippetBuffer(sampleRate = 1_000, durationSeconds = 1.0) // 1000 samples
        // Push 1500 samples: 0..1499
        val samples = ShortArray(1500) { it.toShort() }
        buffer.push(samples)

        // Capture last 500 samples: should be 1000..1499
        val wav = buffer.captureWavSnippet(0.5)
        assertThat(wav.size).isEqualTo(44 + 1000)

        val pcm = ByteBuffer.wrap(wav, 44, 1000).order(ByteOrder.LITTLE_ENDIAN)
        val firstOut = pcm.short
        assertThat(firstOut.toInt()).isEqualTo(1000)
    }
}
