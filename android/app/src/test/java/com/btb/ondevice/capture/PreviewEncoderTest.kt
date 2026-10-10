package com.btb.ondevice.capture

import com.btb.ondevice.pipeline.DegradeLevel
import com.google.common.truth.Truth.assertThat
import org.junit.jupiter.api.Test
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

class PreviewEncoderTest {

    @Test
    fun `offer returns false when disabled`() {
        var encodedCount = 0
        val encoder = PreviewEncoder(
            encodeFn = { _, _, _, _, _, _ -> ByteArray(10) },
            onEncoded = { encodedCount++ }
        )
        encoder.enabled = false
        val accepted = encoder.offer(ByteArray(100), 10, 10, 0)
        assertThat(accepted).isFalse()
        assertThat(encodedCount).isEqualTo(0)
        encoder.shutdown()
    }

    @Test
    fun `offer returns false when fps is zero or negative`() {
        val encoder = PreviewEncoder(
            encodeFn = { _, _, _, _, _, _ -> ByteArray(10) },
            onEncoded = {}
        )
        encoder.config = PreviewConfig(fps = 0.0)
        assertThat(encoder.offer(ByteArray(100), 10, 10, 0)).isFalse()
        encoder.config = PreviewConfig(fps = -1.0)
        assertThat(encoder.offer(ByteArray(100), 10, 10, 0)).isFalse()
        encoder.shutdown()
    }

    @Test
    fun `offer throttles when called within interval`() {
        var fakeTime = 1000L
        val executor = Executors.newSingleThreadExecutor()
        val encoder = PreviewEncoder(
            executor = executor,
            clock = { fakeTime },
            encodeFn = { _, _, _, _, _, _ -> ByteArray(10) },
            onEncoded = {}
        )
        encoder.config = PreviewConfig(fps = 2.0) // 500 ms interval

        // First offer at t=1000 succeeds
        assertThat(encoder.offer(ByteArray(100), 10, 10, 0)).isTrue()

        // Second offer at t=1200 (< 500ms) fails
        fakeTime = 1200L
        assertThat(encoder.offer(ByteArray(100), 10, 10, 0)).isFalse()

        // Third offer at t=1500 (>= 500ms) succeeds
        fakeTime = 1500L
        assertThat(encoder.offer(ByteArray(100), 10, 10, 0)).isTrue()

        encoder.shutdown()
    }

    @Test
    fun `latest frame wins when worker is busy`() {
        val blockWorkerLatch = CountDownLatch(1)
        val firstStartedLatch = CountDownLatch(1)
        val deliveredFrames = mutableListOf<Byte>()
        val allDoneLatch = CountDownLatch(2)

        var fakeTime = 1000L
        val executor = Executors.newSingleThreadExecutor()
        val encoder = PreviewEncoder(
            executor = executor,
            clock = { fakeTime },
            encodeFn = { rgba, _, _, _, _, _ ->
                if (rgba[0] == 1.toByte()) {
                    firstStartedLatch.countDown()
                    blockWorkerLatch.await(2, TimeUnit.SECONDS)
                }
                rgba
            },
            onEncoded = { jpeg ->
                synchronized(deliveredFrames) {
                    deliveredFrames.add(jpeg[0])
                }
                allDoneLatch.countDown()
            }
        )
        encoder.config = PreviewConfig(fps = 100.0) // 10ms interval

        // Frame 1 starts encoding and blocks
        fakeTime = 1000L
        assertThat(encoder.offer(byteArrayOf(1), 1, 1, 0)).isTrue()
        assertThat(firstStartedLatch.await(2, TimeUnit.SECONDS)).isTrue()

        // Frame 2 arrives
        fakeTime = 1020L
        assertThat(encoder.offer(byteArrayOf(2), 1, 1, 0)).isTrue()

        // Frame 3 arrives before worker unblocks -> overrides Frame 2
        fakeTime = 1040L
        assertThat(encoder.offer(byteArrayOf(3), 1, 1, 0)).isTrue()

        // Unblock worker
        blockWorkerLatch.countDown()
        assertThat(allDoneLatch.await(2, TimeUnit.SECONDS)).isTrue()

        // Expect Frame 1 was encoded, Frame 2 was dropped, Frame 3 was encoded
        synchronized(deliveredFrames) {
            assertThat(deliveredFrames).containsExactly(1.toByte(), 3.toByte())
        }
        encoder.shutdown()
    }

    @Test
    fun `encode failure does not throw or break subsequent offers`() {
        val attempts = AtomicInteger(0)
        val delivered = mutableListOf<ByteArray>()
        val firstLatch = CountDownLatch(1)
        val secondLatch = CountDownLatch(1)

        var fakeTime = 1000L
        val executor = Executors.newSingleThreadExecutor()
        val encoder = PreviewEncoder(
            executor = executor,
            clock = { fakeTime },
            encodeFn = { rgba, _, _, _, _, _ ->
                val count = attempts.incrementAndGet()
                if (count == 1) {
                    firstLatch.countDown()
                    throw RuntimeException("Simulated encode OOM/error")
                }
                secondLatch.countDown()
                rgba
            },
            onEncoded = {
                delivered.add(it)
            }
        )
        encoder.config = PreviewConfig(fps = 10.0)

        // First offer fails in worker
        fakeTime = 1000L
        assertThat(encoder.offer(byteArrayOf(10), 1, 1, 0)).isTrue()
        assertThat(firstLatch.await(2, TimeUnit.SECONDS)).isTrue()

        // Second offer after interval succeeds
        fakeTime = 1200L
        assertThat(encoder.offer(byteArrayOf(20), 1, 1, 0)).isTrue()
        assertThat(secondLatch.await(2, TimeUnit.SECONDS)).isTrue()
        Thread.sleep(50) // wait for onEncoded delivery

        assertThat(attempts.get()).isEqualTo(2)
        assertThat(delivered).hasSize(1)
        assertThat(delivered[0][0]).isEqualTo(20.toByte())
        encoder.shutdown()
    }

    @Test
    fun `ladder starts at rung 1 (3 fps)`() {
        val ladder = PreviewLadder()
        assertThat(ladder.currentRungIndex).isEqualTo(1)
        assertThat(ladder.currentConfig()).isEqualTo(PreviewConfig(fps = 3.0, maxEdge = 640, quality = 65))
    }

    @Test
    fun `ladder steps down on thermal degradation`() {
        val ladder = PreviewLadder()
        val cfg = ladder.onFeedback(
            degradeLevel = DegradeLevel.L1_WARM,
            recentBackpressureDropRatio = 0.0,
            nowMs = 10_000L
        )
        assertThat(ladder.currentRungIndex).isEqualTo(2)
        assertThat(cfg).isEqualTo(PreviewConfig(fps = 2.0, maxEdge = 480, quality = 60))
    }

    @Test
    fun `ladder steps down on high backpressure drop ratio`() {
        val ladder = PreviewLadder()
        val cfg = ladder.onFeedback(
            degradeLevel = DegradeLevel.L0_NORMAL,
            recentBackpressureDropRatio = 0.25,
            nowMs = 10_000L
        )
        assertThat(ladder.currentRungIndex).isEqualTo(2)
        assertThat(cfg).isEqualTo(PreviewConfig(fps = 2.0, maxEdge = 480, quality = 60))
    }

    @Test
    fun `ladder clamps at lowest rung`() {
        val ladder = PreviewLadder()
        ladder.onFeedback(DegradeLevel.L3_CRITICAL, 0.0, 1000L) // -> rung 2
        ladder.onFeedback(DegradeLevel.L3_CRITICAL, 0.0, 2000L) // -> rung 3 (lowest: 1 fps, 320, 60)
        val cfg = ladder.onFeedback(DegradeLevel.L3_CRITICAL, 0.0, 3000L) // clamped
        assertThat(ladder.currentRungIndex).isEqualTo(3)
        assertThat(cfg).isEqualTo(PreviewConfig(fps = 1.0, maxEdge = 320, quality = 60))
    }

    @Test
    fun `ladder steps up only after 30s clean`() {
        val ladder = PreviewLadder()
        ladder.onFeedback(DegradeLevel.L2_HOT, 0.0, 10_000L) // stepped down to rung 2 at t=10s
        assertThat(ladder.currentRungIndex).isEqualTo(2)

        // At t=25s (15s clean), still not 30s
        ladder.onFeedback(DegradeLevel.L0_NORMAL, 0.0, 25_000L)
        assertThat(ladder.currentRungIndex).isEqualTo(2)

        // At t=40s (30s clean), steps up to rung 1
        val steppedUp = ladder.onFeedback(DegradeLevel.L0_NORMAL, 0.0, 40_000L)
        assertThat(ladder.currentRungIndex).isEqualTo(1)
        assertThat(steppedUp.fps).isEqualTo(3.0)

        // At t=70s (another 30s clean), steps up to rung 0
        val top = ladder.onFeedback(DegradeLevel.L0_NORMAL, 0.0, 70_000L)
        assertThat(ladder.currentRungIndex).isEqualTo(0)
        assertThat(top.fps).isEqualTo(5.0)

        // At t=100s, clamped at rung 0
        val clamped = ladder.onFeedback(DegradeLevel.L0_NORMAL, 0.0, 100_000L)
        assertThat(ladder.currentRungIndex).isEqualTo(0)
        assertThat(clamped.fps).isEqualTo(5.0)
    }

    @Test
    fun `ladder reset restores target rung`() {
        val ladder = PreviewLadder()
        ladder.onFeedback(DegradeLevel.L3_CRITICAL, 0.0, 1000L)
        ladder.reset(0)
        assertThat(ladder.currentRungIndex).isEqualTo(0)
        assertThat(ladder.currentConfig().fps).isEqualTo(5.0)
    }
}
