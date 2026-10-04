package com.btb.ondevice.vision

import com.google.common.truth.Truth.assertThat
import org.junit.jupiter.api.Test

class BoxTrackerTest {
    @Test
    fun `first box initializes smoothed directly`() {
        val tracker = BoxTracker(alpha = 0.5, resetAfterS = 1.0)
        val raw = Box(100.0, 100.0, 200.0, 200.0)
        val smoothed = tracker.update(raw, ts = 0.0)

        assertThat(smoothed).isEqualTo(raw)
    }

    @Test
    fun `continuous time smoothing blends boxes smoothly`() {
        val tracker = BoxTracker(alpha = 0.5, resetAfterS = 1.0)
        tracker.update(Box(100.0, 100.0, 200.0, 200.0), ts = 0.0)
        val next = Box(120.0, 120.0, 220.0, 220.0)
        val smoothed = tracker.update(next, ts = 0.125)

        assertThat(smoothed).isNotNull()
        // At dt=0.125, tau_s is chosen so alpha is 0.5
        assertThat(smoothed!!.x1).isWithin(1e-4).of(110.0)
        assertThat(smoothed.y1).isWithin(1e-4).of(110.0)
        assertThat(smoothed.x2).isWithin(1e-4).of(210.0)
        assertThat(smoothed.y2).isWithin(1e-4).of(210.0)
    }

    @Test
    fun `box jump with low IoU resets without drag`() {
        val tracker = BoxTracker(alpha = 0.5, resetAfterS = 1.0)
        tracker.update(Box(10.0, 10.0, 50.0, 50.0), ts = 0.0)
        val jump = Box(500.0, 500.0, 600.0, 600.0)
        val smoothed = tracker.update(jump, ts = 0.125)

        assertThat(smoothed).isEqualTo(jump)
    }

    @Test
    fun `misses exceeding resetAfterS clear smoothed box`() {
        val tracker = BoxTracker(alpha = 0.5, resetAfterS = 1.0)
        tracker.update(Box(100.0, 100.0, 200.0, 200.0), ts = 0.0)
        assertThat(tracker.current()).isNotNull()

        tracker.update(null, ts = 0.5)
        // Only 0.5s elapsed, still tracking
        assertThat(tracker.current()).isNotNull()

        tracker.update(null, ts = 1.1)
        // 1.1s > 1.0s resetAfterS -> resets
        assertThat(tracker.current()).isNull()
    }

    @Test
    fun `point in zone detects coordinates correctly`() {
        val zone = listOf(
            listOf(0.2, 0.4),
            listOf(0.8, 0.4),
            listOf(0.8, 0.95),
            listOf(0.2, 0.95),
        )
        // Inside
        assertThat(pointInZone(320.0, 300.0, zone, 640.0, 480.0)).isTrue()
        // Outside (too high)
        assertThat(pointInZone(320.0, 50.0, zone, 640.0, 480.0)).isFalse()
        // Outside (too far left)
        assertThat(pointInZone(50.0, 300.0, zone, 640.0, 480.0)).isFalse()
    }
}
