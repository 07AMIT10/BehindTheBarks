package com.btb.ondevice.capture

/**
 * Adaptive preview quality (T5.2 step 4). Pure JVM: no Android types, and the clock is injected.
 *
 * Rungs, index 0 best. The ladder starts at rung 0 and only lowers the server policy: [effective]
 * takes the per-field minimum of the policy and the rung, so the server policy stays the ceiling.
 *
 * Fed about once a second through [onSample]:
 * - step DOWN one rung (never below the last) when the thermal level got worse since the previous
 *   sample, or when more than 20% of the previews offered in the last 10 s were dropped;
 * - step UP one rung only after 30 s with neither condition AND the thermal level at 0 (normal).
 *   A phone that stays hot at a constant level never steps up; the 30 s clock restarts each second
 *   the level is above 0, so the climb back starts 30 s after the level returns to 0;
 * - never move more than one rung per 5 s, in either direction.
 */
class PreviewLadder(private val clock: () -> Long = System::currentTimeMillis) {
    companion object {
        const val DROP_RATIO_LIMIT = 0.20
        const val DROP_WINDOW_MS = 10_000L
        const val CLEAN_BEFORE_STEP_UP_MS = 30_000L
        const val MIN_MOVE_GAP_MS = 5_000L

        val RUNGS = listOf(
            PreviewConfig(fps = 5.0, maxEdge = 640, quality = 75),
            PreviewConfig(fps = 3.0, maxEdge = 640, quality = 65),
            PreviewConfig(fps = 2.0, maxEdge = 480, quality = 60),
            PreviewConfig(fps = 1.0, maxEdge = 320, quality = 60),
        )
    }

    private class Sample(val atMs: Long, val offered: Int, val dropped: Int)

    private val window = ArrayDeque<Sample>()
    private var lastMoveMs: Long? = null
    private var lastDegradedMs: Long = clock()

    /** Index into [RUNGS]; 0 is the best quality. */
    @Volatile
    var currentRung: Int = 0
        private set

    fun currentConfig(): PreviewConfig = RUNGS[currentRung]

    /**
     * One sample. [thermalDegraded] is true when the thermal level got worse than at the previous
     * sample. [thermalLevel] is the current degrade level as an int (0 = normal, 1 = warm, 2 = hot,
     * 3 = critical); while it is above 0 the ladder never steps up. [offered] and [droppedBackpressure]
     * are the previews offered to and dropped by the uploader since the previous sample (per-sample
     * deltas, not running totals).
     */
    fun onSample(thermalDegraded: Boolean, thermalLevel: Int, offered: Int, droppedBackpressure: Int) {
        val now = clock()
        window.addLast(Sample(now, offered, droppedBackpressure))
        while (window.isNotEmpty() && now - window.first().atMs >= DROP_WINDOW_MS) window.removeFirst()
        val offeredSum = window.sumOf { it.offered.toLong() }
        val droppedSum = window.sumOf { it.dropped.toLong() }
        val dropRatio = if (offeredSum > 0) droppedSum.toDouble() / offeredSum else 0.0

        if (thermalDegraded || dropRatio > DROP_RATIO_LIMIT) {
            lastDegradedMs = now
            if (currentRung < RUNGS.lastIndex && mayMove(now)) {
                currentRung++
                lastMoveMs = now
            }
        } else if (thermalLevel > 0) {
            lastDegradedMs = now // hot but not worsening: no step down, and no step up either
        } else if (currentRung > 0 && now - lastDegradedMs >= CLEAN_BEFORE_STEP_UP_MS && mayMove(now)) {
            currentRung--
            lastMoveMs = now
            lastDegradedMs = now // the next step up needs another 30 s clean
        }
    }

    /** The policy value lowered by the current rung, field by field. */
    fun effective(policy: PreviewConfig): PreviewConfig {
        val rung = currentConfig()
        return PreviewConfig(
            fps = minOf(policy.fps, rung.fps),
            maxEdge = minOf(policy.maxEdge, rung.maxEdge),
            quality = minOf(policy.quality, rung.quality),
        )
    }

    private fun mayMove(now: Long): Boolean = lastMoveMs?.let { now - it >= MIN_MOVE_GAP_MS } ?: true
}
