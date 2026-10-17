package com.btb.ondevice.net

import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference

/** One bioacoustic snippet waiting to be POSTed. */
data class SnippetUpload(val snippetId: String, val wav: ByteArray) {
    override fun toString(): String = "SnippetUpload(id=$snippetId, bytes=${wav.size})"
}

/**
 * I2: keeps at most ONE snippet POST in flight, with a single `newest wins` waiting slot.
 *
 * A barking dog can trigger a debounced burst of snippets while the server is stalled; without
 * this gate those POSTs park on the IO scope and starve pings/reconnect/spool work. The policy:
 *
 *  - an arriving snippet starts immediately when nothing is in flight;
 *  - otherwise it takes the one waiting slot (dropping whatever stale snippet sits there) or is
 *    dropped itself when the slot is exhaustively replaced;
 *  - when a POST finishes, the waiting snippet starts and the gate is held again.
 *
 * Pure (no OkHttp, no threads of its own): the caller injects [startPost] - it must not call the
 * gate back synchronously with completion, it hands the work to its own executor. Snippets are
 * best effort: any dropped one is counted, never queued up.
 */
class SnippetGate(private val startPost: (SnippetUpload) -> Unit) {

    private val inFlight = AtomicBoolean(false)
    private val waiting = AtomicReference<SnippetUpload?>(null)
    private val droppedCount = java.util.concurrent.atomic.AtomicInteger(0)

    /** Snippets dropped in favour of a newer one (monotonic). */
    val dropped: Int get() = droppedCount.get()

    /** True while a POST is out and the gate is busy. Exposed for tests and diagnostics. */
    val busy: Boolean get() = inFlight.get()

    fun accept(snippet: SnippetUpload) {
        if (inFlight.compareAndSet(false, true)) {
            startPost(snippet)
            return
        }
        // Gate busy: the newest snippet only has to wait if the slot is empty, otherwise it
        // replaces whatever older one is queued up there.
        if (waiting.getAndSet(snippet) != null) droppedCount.incrementAndGet()
    }

    /** Must be called exactly when the current POST is done, whatever its outcome. */
    fun completePost() {
        // Drain the waiting slot while the gate stays held: the completion of each POST
        // starts the next one, so the burst serialises but the newest always wins the slot.
        val next = waiting.getAndSet(null)
        if (next != null) {
            startPost(next)
            return
        }
        // Nothing waiting: release the gate ...
        inFlight.set(false)
        // ... but a snippet can land in the tiny window between the drain and the release.
        val late = waiting.getAndSet(null)
        if (late != null) {
            if (inFlight.compareAndSet(false, true)) {
                startPost(late)
            } else if (!waiting.compareAndSet(null, late)) {
                // Somebody else already holds the gate and put its own in the slot: this one is
                // the stale loser, count it rather than stranding it.
                droppedCount.incrementAndGet()
            }
        }
    }
}
