package com.btb.ondevice.ui

import com.google.common.truth.Truth.assertThat
import org.junit.jupiter.api.Test

class OverlayThrottleTest {

    private fun state(label: String?, bboxX: Float = 0.1f) = OverlayState(
        bbox = floatArrayOf(bboxX, 0.2f, 0.5f, 0.6f),
        keypoints = mapOf("Nose" to floatArrayOf(0.3f, 0.3f, 0.9f)),
        emotionLabel = label,
        confidence = 0.8f,
        degradeLevel = "NORMAL",
    )

    @Test
    fun `the first state is emitted at once`() {
        val throttle = OverlayThrottle()
        assertThat(throttle.offer(0L, state("HAPPY"))).isNotNull()
    }

    @Test
    fun `an identical state with new array instances is not emitted`() {
        val throttle = OverlayThrottle()
        throttle.offer(0L, state("HAPPY"))
        // Same content, fresh arrays: a data-class == would call this a change.
        assertThat(throttle.offer(1_000L, state("HAPPY"))).isNull()
    }

    @Test
    fun `a change inside the window is held back until the window passes`() {
        val throttle = OverlayThrottle(minIntervalMs = 250L)
        throttle.offer(0L, state("HAPPY"))
        assertThat(throttle.offer(100L, state("EXCITED"))).isNull()
        assertThat(throttle.offer(249L, state("EXCITED"))).isNull()
        assertThat(throttle.offer(250L, state("EXCITED"))?.emotionLabel).isEqualTo("EXCITED")
    }

    @Test
    fun `at most about four changes per second reach the UI`() {
        val throttle = OverlayThrottle()
        var emitted = 0
        // 15 fps of changing states for one second (one every ~66 ms).
        for (frame in 0 until 15) {
            val now = frame * 1_000L / 15
            if (throttle.offer(now, state("L$frame", bboxX = frame / 100f)) != null) emitted++
        }
        assertThat(emitted).isAtMost(5)
        assertThat(emitted).isAtLeast(3)
    }

    @Test
    fun `reverting to the emitted state inside the window emits nothing`() {
        val throttle = OverlayThrottle()
        throttle.offer(0L, state("HAPPY"))
        throttle.offer(50L, state("EXCITED"))
        assertThat(throttle.offer(100L, state("HAPPY"))).isNull()
    }

    @Test
    fun `a keypoint change is a change`() {
        val throttle = OverlayThrottle()
        throttle.offer(0L, state("HAPPY"))
        val moved = state("HAPPY").copy(keypoints = mapOf("Nose" to floatArrayOf(0.4f, 0.3f, 0.9f)))
        assertThat(throttle.offer(300L, moved)).isNotNull()
    }

    @Test
    fun `a removed keypoint is a change`() {
        val a = state("HAPPY")
        val b = a.copy(keypoints = null)
        assertThat(a.drawsSameAs(b)).isFalse()
        assertThat(b.drawsSameAs(a)).isFalse()
        assertThat(b.drawsSameAs(b.copy())).isTrue()
    }
}
