package com.btb.ondevice.ui

/**
 * True when two overlay states would draw the same pixels. FloatArray and Map values are compared
 * by content: `OverlayState` is a data class, but its array fields compare by identity, so a
 * plain `==` would call every frame "changed".
 */
internal fun OverlayState.drawsSameAs(other: OverlayState): Boolean {
    if (emotionLabel != other.emotionLabel) return false
    if (confidence != other.confidence) return false
    if (latencyMs != other.latencyMs) return false
    if (fps != other.fps) return false
    if (degradeLevel != other.degradeLevel) return false
    if (!bbox.contentEquals(other.bbox)) return false
    if (!wagRoi.contentEquals(other.wagRoi)) return false
    val a = keypoints
    val b = other.keypoints
    if (a == null || b == null) return a == null && b == null
    if (a.size != b.size) return false
    for ((name, point) in a) {
        val counterpart = b[name] ?: return false
        if (!point.contentEquals(counterpart)) return false
    }
    return true
}

/**
 * Limits how often the perception loop may push an overlay to the UI thread.
 *
 * The camera runs at about 15 fps, and every inference result used to post a UI update and a
 * full-screen invalidation. That kept the main thread busy and the accessibility tree changing, so
 * uiautomator could never reach an idle state. Here:
 *  - nothing is emitted while the state is unchanged (an identical frame costs nothing), and
 *  - at most one change is emitted per [minIntervalMs] (4 Hz by default). A change that arrives
 *    inside the window is held back; the next offer after the window emits the latest state.
 *
 * Pure Kotlin with an injected clock, so it is unit-tested on the JVM.
 */
internal class OverlayThrottle(private val minIntervalMs: Long = DEFAULT_INTERVAL_MS) {
    private var lastEmitted: OverlayState? = null
    private var lastEmitAtMs: Long? = null

    /** Returns the state to draw now, or null when the UI should not be touched. */
    @Synchronized
    fun offer(nowMs: Long, state: OverlayState): OverlayState? {
        val emitted = lastEmitted
        if (emitted != null && emitted.drawsSameAs(state)) return null
        val at = lastEmitAtMs
        if (at != null && nowMs - at < minIntervalMs) return null
        lastEmitted = state
        lastEmitAtMs = nowMs
        return state
    }

    companion object {
        const val DEFAULT_INTERVAL_MS = 250L
    }
}
