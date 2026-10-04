package com.btb.ondevice.vision

import com.google.common.truth.Truth.assertThat
import org.junit.jupiter.api.Test
import kotlin.math.sin

class WagEstimatorTest {
    @Test
    fun `sinusoid oscillation at 3_0 Hz is estimated within 0_05 Hz`() {
        val fps = 15.0
        val durationS = 3.0
        val count = (fps * durationS).toInt()
        val freq = 3.0

        val t = mutableListOf<Double>()
        val sig = mutableListOf<Double>()
        for (i in 0 until count) {
            val ts = i / fps
            t.add(ts)
            sig.add(0.15 * sin(2.0 * Math.PI * freq * ts))
        }

        val est = estimateWagHz(t, sig)
        assertThat(est).isNotNull()
        assertThat(est!!).isWithin(0.05).of(freq)
    }

    @Test
    fun `sinusoid oscillation at 2_0 Hz is estimated within 0_05 Hz`() {
        val fps = 15.0
        val durationS = 3.0
        val count = (fps * durationS).toInt()
        val freq = 2.0

        val t = mutableListOf<Double>()
        val sig = mutableListOf<Double>()
        for (i in 0 until count) {
            val ts = i / fps
            t.add(ts)
            sig.add(0.2 * sin(2.0 * Math.PI * freq * ts))
        }

        val est = estimateWagHz(t, sig)
        assertThat(est).isNotNull()
        assertThat(est!!).isWithin(0.05).of(freq)
    }

    @Test
    fun `low amplitude noise below minStd returns 0_0 Hz`() {
        val t = (0 until 45).map { it / 15.0 }
        val sig = (0 until 45).map { 0.001 * (it % 3 - 1) }

        val est = estimateWagHz(t, sig)
        assertThat(est).isEqualTo(0.0)
    }

    @Test
    fun `insufficient samples returns null`() {
        val t = listOf(0.0, 0.1, 0.2)
        val sig = listOf(0.1, 0.2, 0.1)

        val est = estimateWagHz(t, sig)
        assertThat(est).isNull()
    }

    @Test
    fun `computeRoiShift computes horizontal shift`() {
        val w = 64
        val h = 64
        val prev = Array(h) { ByteArray(w) }
        val curr = Array(h) { ByteArray(w) }

        // Light up column 40 in curr vs column 20 in prev
        for (y in 0 until h) {
            curr[y][40] = 100.toByte()
            prev[y][20] = 100.toByte()
        }

        val (shift, motion) = computeRoiShift(curr, prev)
        assertThat(motion).isGreaterThan(0.0)
        // Difference is non-zero
        assertThat(shift).isNotEqualTo(0.0)
    }
}
