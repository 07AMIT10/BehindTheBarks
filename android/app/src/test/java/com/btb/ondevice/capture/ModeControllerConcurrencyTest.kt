package com.btb.ondevice.capture

import com.google.common.truth.Truth.assertThat
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference
import org.junit.jupiter.api.Test

/**
 * T3.2 / plan item 7: the network thread (policy, mode_request) and the main thread (device cap, prompt
 * answers, approval timeout) share one ModeController. These tests hammer it from many threads and check
 * the invariants that must hold at every moment, not just at the end.
 */
class ModeControllerConcurrencyTest {

    private val allModes = listOf(ViewMode.BLACKOUT, ViewMode.PRIVACY, ViewMode.LIMITED, ViewMode.FULL)

    @Test
    fun `many threads mixing policy, requests, answers and cap changes never break the invariants`() {
        val clock = java.util.concurrent.atomic.AtomicLong(1_000L)
        val c = ModeController(ViewMode.PRIVACY, clock = { clock.get() })
        c.raiseDurationMs = 5_000L
        c.requestTimeoutMs = 3_000L
        val failure = AtomicReference<Throwable?>(null)
        val threads = 16
        val iterations = 4_000
        val start = CountDownLatch(1)
        val pool = Executors.newFixedThreadPool(threads)
        repeat(threads) { t ->
            pool.execute {
                try {
                    start.await()
                    for (i in 0 until iterations) {
                        val mode = allModes[(i + t) % allModes.size]
                        when ((i * 7 + t) % 9) {
                            0 -> c.onPolicy(Policy(mode = mode, videoViewers = i % 2))
                            1 -> c.onModeRequest(mode, fromOwner = i % 3 == 0)
                            2 -> c.resolvePendingRaise(RaiseResult.ALLOWED)
                            3 -> c.resolvePendingRaise(RaiseResult.DENIED)
                            4 -> c.resolvePendingRaise(RaiseResult.TIMEOUT)
                            5 -> if (i % 50 == 0) c.setStationCapFromDevice(mode)
                            6 -> clock.addAndGet(137L)
                            7 -> c.pendingRaise()
                            else -> c.reportedCap()
                        }
                        // The invariants, checked on one snapshot so a concurrent change cannot fake a violation.
                        val snap = c.snapshot()
                        check(snap.effective.rank <= snap.policy.rank) { "effective ${snap.effective} above policy ${snap.policy}" }
                        check(snap.reason == ModeReason.APPROVED_RAISE || snap.effective.rank <= snap.cap.rank) {
                            "effective ${snap.effective} above cap ${snap.cap} without an approval"
                        }
                    }
                } catch (t2: Throwable) {
                    failure.compareAndSet(null, t2)
                }
            }
        }
        start.countDown()
        pool.shutdown()
        assertThat(pool.awaitTermination(60, TimeUnit.SECONDS)).isTrue()
        failure.get()?.let { throw it }
    }

    @Test
    fun `an approved raise never lifts exposure above the cap the person chose plus the server policy`() {
        val clock = java.util.concurrent.atomic.AtomicLong(0L)
        val c = ModeController(ViewMode.PRIVACY, clock = { clock.get() })
        c.onPolicy(Policy(mode = ViewMode.FULL))
        val pool = Executors.newFixedThreadPool(8)
        val start = CountDownLatch(1)
        val violation = AtomicReference<String?>(null)
        repeat(8) {
            pool.execute {
                start.await()
                for (i in 0 until 5_000) {
                    if (i % 2 == 0) c.onModeRequest(ViewMode.FULL, fromOwner = false) else c.resolvePendingRaise(RaiseResult.ALLOWED)
                    if (i % 97 == 0) clock.addAndGet(20_000L)
                    val snap = c.snapshot()
                    if (snap.reason != ModeReason.APPROVED_RAISE && snap.effective.rank > snap.cap.rank) {
                        violation.compareAndSet(null, "effective ${snap.effective} above the cap without an approval")
                    }
                }
            }
        }
        start.countDown()
        pool.shutdown()
        assertThat(pool.awaitTermination(60, TimeUnit.SECONDS)).isTrue()
        assertThat(violation.get()).isNull()
    }
}
