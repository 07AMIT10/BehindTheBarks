package com.btb.ondevice.capture

import com.google.common.truth.Truth.assertThat
import org.junit.jupiter.api.Test

class PreviewLadderTest {
    private var nowMs = 0L
    private val ladder = PreviewLadder { nowMs }

    /** Advances the fake clock to [atMs] and feeds one sample. */
    private fun sample(
        atMs: Long,
        thermalDegraded: Boolean = false,
        thermalLevel: Int = 0,
        offered: Int = 0,
        dropped: Int = 0,
    ) {
        nowMs = atMs
        ladder.onSample(thermalDegraded, thermalLevel, offered, dropped)
    }

    @Test
    fun `starts at rung 0, the best quality`() {
        assertThat(ladder.currentRung).isEqualTo(0)
        assertThat(ladder.currentConfig()).isEqualTo(PreviewConfig(fps = 5.0, maxEdge = 640, quality = 75))
    }

    @Test
    fun `thermal worsening steps down one rung`() {
        sample(0, thermalDegraded = true)
        assertThat(ladder.currentRung).isEqualTo(1)
        assertThat(ladder.currentConfig()).isEqualTo(PreviewConfig(fps = 3.0, maxEdge = 640, quality = 65))
    }

    @Test
    fun `more than 20 percent dropped over 10 s steps down`() {
        sample(0, offered = 10, dropped = 3) // 30%
        assertThat(ladder.currentRung).isEqualTo(1)
    }

    @Test
    fun `exactly 20 percent dropped does not step down`() {
        sample(0, offered = 10, dropped = 2)
        assertThat(ladder.currentRung).isEqualTo(0)
    }

    @Test
    fun `drops older than 10 s no longer count`() {
        sample(0, offered = 10, dropped = 5) // 50%: rung 1
        assertThat(ladder.currentRung).isEqualTo(1)
        sample(11_000, offered = 10, dropped = 0) // the t=0 window has aged out: 0% over 10 s
        assertThat(ladder.currentRung).isEqualTo(1)
    }

    @Test
    fun `clean 30 s steps up one rung`() {
        sample(0, thermalDegraded = true) // rung 1
        for (t in 1L..29L) sample(t * 1_000)
        assertThat(ladder.currentRung).isEqualTo(1)
        sample(30_000)
        assertThat(ladder.currentRung).isEqualTo(0)
    }

    @Test
    fun `sustained hot level never steps up`() {
        sample(0, thermalDegraded = true, thermalLevel = 2) // rung 1
        for (t in 1L..300L) sample(t * 1_000, thermalLevel = 2) // hot and constant: no worsening
        assertThat(ladder.currentRung).isEqualTo(1)
        for (t in 301L..400L) sample(t * 1_000, thermalLevel = 1) // still warm: still no step up
        assertThat(ladder.currentRung).isEqualTo(1)
    }

    @Test
    fun `steps up 30 s after the level returns to 0`() {
        sample(0, thermalDegraded = true, thermalLevel = 1) // rung 1
        for (t in 1L..60L) sample(t * 1_000, thermalLevel = 1) // hot until t=60 s
        for (t in 61L..89L) sample(t * 1_000, thermalLevel = 0) // normal again, only 29 s clean
        assertThat(ladder.currentRung).isEqualTo(1)
        sample(90_000, thermalLevel = 0) // 30 s clean since the last hot sample at t=60 s
        assertThat(ladder.currentRung).isEqualTo(0)
    }

    @Test
    fun `a step up needs another 30 s clean before the next one`() {
        sample(0, thermalDegraded = true)
        sample(5_000, thermalDegraded = true) // rung 2
        sample(10_000, thermalDegraded = true) // rung 3
        sample(40_000) // 30 s clean since t=10: rung 2
        assertThat(ladder.currentRung).isEqualTo(2)
        sample(50_000)
        assertThat(ladder.currentRung).isEqualTo(2)
        sample(70_000) // another 30 s clean since t=40
        assertThat(ladder.currentRung).isEqualTo(1)
    }

    @Test
    fun `never moves more than one rung per 5 s when the trouble persists`() {
        for (t in 0L..9L) sample(t * 1_000, thermalDegraded = true)
        assertThat(ladder.currentRung).isEqualTo(2) // t=0 -> 1, t=5 -> 2, nothing before t=10
        sample(10_000, thermalDegraded = true)
        assertThat(ladder.currentRung).isEqualTo(3)
    }

    @Test
    fun `no step at all within 5 s of the last move`() {
        sample(0, thermalDegraded = true) // rung 1
        sample(4_999, thermalDegraded = true)
        assertThat(ladder.currentRung).isEqualTo(1)
        sample(5_000, thermalDegraded = true)
        assertThat(ladder.currentRung).isEqualTo(2)
    }

    @Test
    fun `floor holds at rung 3 under constant trouble`() {
        for (t in 0L..60L) sample(t * 1_000, thermalDegraded = true)
        assertThat(ladder.currentRung).isEqualTo(3)
        assertThat(ladder.currentConfig()).isEqualTo(PreviewConfig(fps = 1.0, maxEdge = 320, quality = 60))
    }

    @Test
    fun `ceiling holds at rung 0 under long clean periods`() {
        for (t in 0L..300L) sample(t * 1_000)
        assertThat(ladder.currentRung).isEqualTo(0)
        assertThat(ladder.currentConfig().fps).isEqualTo(5.0)
    }

    @Test
    fun `effective takes the minimum per field against a 3 fps 640 70 policy`() {
        val policy = PreviewConfig(fps = 3.0, maxEdge = 640, quality = 70)
        assertThat(ladder.effective(policy)).isEqualTo(PreviewConfig(fps = 3.0, maxEdge = 640, quality = 70))
        sample(0, thermalDegraded = true) // rung 1: 3 fps, 640, q65
        assertThat(ladder.effective(policy)).isEqualTo(PreviewConfig(fps = 3.0, maxEdge = 640, quality = 65))
        sample(5_000, thermalDegraded = true) // rung 2: 2 fps, 480, q60
        assertThat(ladder.effective(policy)).isEqualTo(PreviewConfig(fps = 2.0, maxEdge = 480, quality = 60))
    }

    @Test
    fun `effective keeps a lower server policy when the rung is higher quality`() {
        val policy = PreviewConfig(fps = 0.5, maxEdge = 240, quality = 50)
        assertThat(ladder.effective(policy)).isEqualTo(PreviewConfig(fps = 0.5, maxEdge = 240, quality = 50))
        sample(0, thermalDegraded = true)
        sample(5_000, thermalDegraded = true)
        sample(10_000, thermalDegraded = true)
        assertThat(ladder.currentRung).isEqualTo(3)
        assertThat(ladder.effective(policy)).isEqualTo(PreviewConfig(fps = 0.5, maxEdge = 240, quality = 50))
    }
}
