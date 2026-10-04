package com.btb.ondevice.capture

import com.google.common.truth.Truth.assertThat
import org.junit.jupiter.api.Test

class CaptureConfigTest {

    @Test
    fun `selectFpsRange picks lowest upper bound containing 15 fps`() {
        val ranges = listOf(
            FpsRange(30, 30),
            FpsRange(15, 30),
            FpsRange(10, 15),
            FpsRange(7, 30),
        )

        val selected = CaptureConfig.selectFpsRange(ranges, targetFps = 15)
        // Both (15, 30), (10, 15), and (7, 30) contain 15. Lowest upper bound is 15 -> (10, 15).
        assertThat(selected).isEqualTo(FpsRange(10, 15))
    }

    @Test
    fun `selectFpsRange falls back to range containing 30 when 15 not available`() {
        val ranges = listOf(
            FpsRange(30, 30),
            FpsRange(24, 24),
            FpsRange(60, 60),
        )

        val selected = CaptureConfig.selectFpsRange(ranges, targetFps = 15)
        // 15 is not covered; fallback to range containing 30
        assertThat(selected).isEqualTo(FpsRange(30, 30))
    }

    @Test
    fun `selectFpsRange returns lowest range if neither 15 nor 30 available`() {
        val ranges = listOf(
            FpsRange(45, 60),
            FpsRange(50, 50),
        )

        val selected = CaptureConfig.selectFpsRange(ranges, targetFps = 15)
        assertThat(selected).isEqualTo(FpsRange(50, 50))
    }

    @Test
    fun `getRotatedDimensions swaps width and height for 90 and 270 degrees`() {
        assertThat(CaptureConfig.getRotatedDimensions(640, 480, 0)).isEqualTo(Pair(640, 480))
        assertThat(CaptureConfig.getRotatedDimensions(640, 480, 90)).isEqualTo(Pair(480, 640))
        assertThat(CaptureConfig.getRotatedDimensions(640, 480, 180)).isEqualTo(Pair(640, 480))
        assertThat(CaptureConfig.getRotatedDimensions(640, 480, 270)).isEqualTo(Pair(480, 640))
        // Negative / modulo handling
        assertThat(CaptureConfig.getRotatedDimensions(640, 480, -90)).isEqualTo(Pair(480, 640))
    }

    @Test
    fun `mapSensorPointToDisplay rotates coordinates correctly`() {
        // Point (0.2, 0.8)
        val (x0, y0) = CaptureConfig.mapSensorPointToDisplay(0.2f, 0.8f, 0)
        assertThat(x0).isEqualTo(0.2f)
        assertThat(y0).isEqualTo(0.8f)

        val (x90, y90) = CaptureConfig.mapSensorPointToDisplay(0.2f, 0.8f, 90)
        assertThat(x90).isWithin(1e-5f).of(0.2f) // 1 - 0.8 = 0.2
        assertThat(y90).isWithin(1e-5f).of(0.2f) // x = 0.2

        val (x180, y180) = CaptureConfig.mapSensorPointToDisplay(0.2f, 0.8f, 180)
        assertThat(x180).isWithin(1e-5f).of(0.8f) // 1 - 0.2 = 0.8
        assertThat(y180).isWithin(1e-5f).of(0.2f) // 1 - 0.8 = 0.2
    }

    @Test
    fun `timestampNsToEpochS converts monotonic nanoseconds with boot offset`() {
        val bootOffsetS = 1700000000.0
        val timestampNs = 5_000_000_000L // 5 seconds since boot
        val epoch = CaptureConfig.timestampNsToEpochS(timestampNs, bootOffsetS)
        assertThat(epoch).isWithin(1e-4).of(1700000005.0)
    }
}
