package com.btb.ondevice.capture

import java.util.ArrayDeque
import java.util.concurrent.atomic.AtomicInteger

/**
 * I5: bounded, newest-wins queue of PCM voice chunks handed from the WebSocket reader thread to
 * the dedicated single playback thread.
 *
 * [MonitorService.playVoiceChunk] runs on the socket's reader thread, where parking is not allowed
 * (treat/torch/policy downlinks and pings ride behind voice there). This queue is the only book
 * keeping the producer does: it is capped at a handful of chunks, and when it is full the STALEST
 * chunk is dropped so the newest speech reaches the dog. Drops are counted, never logged loudly.
 */
class VoiceChunkQueue(private val capacity: Int) {

    init {
        require(capacity > 0) { "voice queue needs a positive capacity" }
    }

    private val chunks = ArrayDeque<ByteArray>()
    private val lock = Any()
    private val droppedCount = AtomicInteger(0)

    /** Chunks discarded because the queue was full (monotonic). */
    val drops: Int get() = droppedCount.get()

    /** Chunks waiting right now (<= [capacity]). */
    val depth: Int get() = synchronized(lock) { chunks.size }

    /**
     * Enqueue a chunk. Never blocks and never refuses: with a full queue the oldest chunk is
     * dropped (counted) to make room, because in speech playback a late chunk is stale.
     */
    fun offer(pcmBytes: ByteArray) {
        if (pcmBytes.isEmpty()) return
        synchronized(lock) {
            while (chunks.size >= capacity) {
                chunks.removeFirst()
                droppedCount.incrementAndGet()
            }
            chunks.addLast(pcmBytes)
        }
    }

    /** Take the oldest chunk for playback, or null when everything queued is already handled. */
    fun poll(): ByteArray? = synchronized(lock) { chunks.pollFirst() }

    /** Drop everything waiting (service shutdown). */
    fun clear() {
        // Dropped on shutdown does not count against the playback drops: nothing was starved for it.
        synchronized(lock) { chunks.clear() }
    }
}
