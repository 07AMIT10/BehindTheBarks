package com.btb.ondevice.capture

import com.google.common.truth.Truth.assertThat
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference
import org.junit.jupiter.api.Test

/** I5: the bounded newest-wins producer/consumer queue for voice downlink chunks. */
class VoiceChunkQueueTest {

    private fun chunk(id: Byte) = ByteArray(4) { 0 }.also { it[0] = id }

    @Test
    fun `capacity is respected while offering below it`() {
        val q = VoiceChunkQueue(3)
        q.offer(chunk(1))
        q.offer(chunk(2))
        assertThat(q.depth).isEqualTo(2)
        assertThat(q.drops).isEqualTo(0)
    }

    @Test
    fun `overflow drops the staliest chunk and keeps the newest`() {
        val q = VoiceChunkQueue(3)
        (1..5).forEach { q.offer(chunk(it.toByte())) }
        assertThat(q.depth).isEqualTo(3)
        assertThat(q.drops).isEqualTo(2)

        assertThat(q.poll()!![0]).isEqualTo(3.toByte())
        assertThat(q.poll()!![0]).isEqualTo(4.toByte())
        assertThat(q.poll()!![0]).isEqualTo(5.toByte())
        assertThat(q.poll()).isNull()
    }

    @Test
    fun `empty chunks are never queued`() {
        val q = VoiceChunkQueue(2)
        q.offer(ByteArray(0))
        assertThat(q.depth).isEqualTo(0)
    }

    @Test
    fun `producer and consumer on different threads never exceed capacity or lose unbounded work`() {
        val q = VoiceChunkQueue(5)
        val failure = AtomicReference<Throwable?>(null)
        val opsPerThread = 5_000
        val start = CountDownLatch(1)
        val done = CountDownLatch(4)
        val consumed = java.util.concurrent.atomic.AtomicInteger(0)

        repeat(2) {
            Thread {
                try {
                    start.await()
                    repeat(opsPerThread) { q.offer(byteArrayOf(it.toByte(), 1)) }
                } catch (t: Throwable) {
                    failure.compareAndSet(null, t)
                } finally {
                    done.countDown()
                }
            }.start()
        }
        repeat(2) {
            Thread {
                try {
                    start.await()
                    repeat(opsPerThread) {
                        q.poll()?.let { consumed.incrementAndGet() }
                    }
                } catch (t: Throwable) {
                    failure.compareAndSet(null, t)
                } finally {
                    done.countDown()
                }
            }.start()
        }

        start.countDown()
        assertThat(done.await(60, TimeUnit.SECONDS)).isTrue()

        // Invariants: never deeper than the capacity, everything offered is either consumed,
        // still queued or dropped-on-full (counted, not silent), and nothing crashed.
        assertThat(q.depth).isAtMost(5)
        assertThat(consumed.get() + q.depth + q.drops).isEqualTo(2 * opsPerThread)
        failure.get()?.let { throw it }
    }
}
