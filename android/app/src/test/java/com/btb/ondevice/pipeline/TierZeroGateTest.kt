package com.btb.ondevice.pipeline

import com.google.common.truth.Truth.assertThat
import org.junit.jupiter.api.Test
import kotlin.math.sin

class TierZeroGateTest {

    @Test
    fun `first frame is always active`() {
        val gate = TierZeroGate(diffThreshold = 0.02)
        val frame = ByteArray(160 * 160) { 100.toByte() }

        val result = gate.evaluate(frame, null)
        assertThat(result.visionActive).isTrue()
        assertThat(result.frameDiff).isEqualTo(1.0)
    }

    @Test
    fun `identical consecutive frame is idle`() {
        val gate = TierZeroGate(diffThreshold = 0.02)
        val frame1 = ByteArray(160 * 160) { 100.toByte() }
        val frame2 = ByteArray(160 * 160) { 100.toByte() }

        gate.evaluate(frame1, null)
        val result = gate.evaluate(frame2, null)

        assertThat(result.visionActive).isFalse()
        assertThat(result.frameDiff).isEqualTo(0.0)
        assertThat(result.anyActive).isFalse()
    }

    @Test
    fun `frame with movement exceeds threshold and is active`() {
        val gate = TierZeroGate(diffThreshold = 0.02)
        val frame1 = ByteArray(160 * 160) { 50.toByte() }
        val frame2 = ByteArray(160 * 160) { 50.toByte() }

        // Change 20% of pixels by 60 levels (e.g., dog moving into frame)
        // Expected diff: 0.2 * 60 / 255 = 12 / 255 = 0.047 > 0.02
        val changedPixels = (160 * 160 * 0.2).toInt()
        for (i in 0 until changedPixels) {
            frame2[i] = 110.toByte()
        }

        gate.evaluate(frame1, null)
        val result = gate.evaluate(frame2, null)

        assertThat(result.visionActive).isTrue()
        assertThat(result.frameDiff).isGreaterThan(0.02)
        assertThat(result.anyActive).isTrue()
    }

    @Test
    fun `audio silence is idle and audio burst is active`() {
        val gate = TierZeroGate(silenceRms = 0.005)

        // Silent audio (all zeros)
        val silentAudio = FloatArray(1600) { 0.0f }
        val res1 = gate.evaluate(null, silentAudio)
        assertThat(res1.audioActive).isFalse()
        assertThat(res1.audioRms).isEqualTo(0.0)

        // Low background noise below silence threshold
        val quietAudio = FloatArray(1600) { 0.001f }
        val res2 = gate.evaluate(null, quietAudio)
        assertThat(res2.audioActive).isFalse()
        assertThat(res2.audioRms).isLessThan(0.005)

        // Loud bark burst (RMS ~ 0.07)
        val loudAudio = FloatArray(1600) { i -> (0.1 * sin(2.0 * Math.PI * 400.0 * i / 16000.0)).toFloat() }
        val res3 = gate.evaluate(null, loudAudio)
        assertThat(res3.audioActive).isTrue()
        assertThat(res3.audioRms).isGreaterThan(0.005)
    }

    @Test
    fun `pcm16 RMS matches float RMS within precision`() {
        val gate = TierZeroGate()
        val n = 1000
        val pcm16 = ShortArray(n) { i -> (10000.0 * sin(2.0 * Math.PI * 300.0 * i / 16000.0)).toInt().toShort() }
        val floatSamples = FloatArray(n) { i -> pcm16[i].toFloat() / 32768.0f }

        val rmsPcm = gate.computeAudioRmsPcm16(pcm16)
        val rmsFloat = gate.computeAudioRms(floatSamples)

        assertThat(rmsPcm).isWithin(1e-4).of(rmsFloat)
    }

    @Test
    fun `reset clears previous frame`() {
        val gate = TierZeroGate(diffThreshold = 0.02)
        val frame = ByteArray(160 * 160) { 100.toByte() }

        gate.evaluate(frame, null)
        gate.reset()

        // After reset, same frame is treated as first frame
        val result = gate.evaluate(frame, null)
        assertThat(result.visionActive).isTrue()
        assertThat(result.frameDiff).isEqualTo(1.0)
    }

    @Test
    fun `disabled gate always reports active`() {
        val gate = TierZeroGate(enabled = false)
        val frame = ByteArray(160 * 160) { 100.toByte() }
        val silentAudio = FloatArray(1600) { 0.0f }

        gate.evaluate(frame, silentAudio)
        val result = gate.evaluate(frame, silentAudio)

        assertThat(result.visionActive).isTrue()
        assertThat(result.audioActive).isTrue()
        assertThat(result.anyActive).isTrue()
    }
}
