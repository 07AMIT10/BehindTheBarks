// Port of backend/vision/detect.py (_SmoothBox and box EMA tracking)
// Commit: 3683457991777e5c22e573bdb472a70202ac5ddb
package com.btb.ondevice.vision

import kotlin.math.exp
import kotlin.math.ln
import kotlin.math.max
import kotlin.math.min

data class Box(val x1: Double, val y1: Double, val x2: Double, val y2: Double) {
    val width: Double get() = max(0.0, x2 - x1)
    val height: Double get() = max(0.0, y2 - y1)
    val area: Double get() = width * height
    fun toList(): List<Double> = listOf(x1, y1, x2, y2)

    companion object {
        fun fromList(l: List<Double>): Box = Box(l[0], l[1], l[2], l[3])
    }
}

fun boxIou(a: Box, b: Box): Double {
    val ix = max(0.0, min(a.x2, b.x2) - max(a.x1, b.x1))
    val iy = max(0.0, min(a.y2, b.y2) - max(a.y1, b.y1))
    val inter = ix * iy
    val union = a.area + b.area - inter
    return if (union > 0.0) inter / union else 0.0
}

fun pointInZone(px: Double, py: Double, zoneNorm: List<List<Double>>, width: Double, height: Double): Boolean {
    if (zoneNorm.size < 3) return false
    val poly = zoneNorm.map { listOf(it[0] * width, it[1] * height) }
    var inside = false
    var j = poly.size - 1
    for (i in poly.indices) {
        val xi = poly[i][0]
        val yi = poly[i][1]
        val xj = poly[j][0]
        val yj = poly[j][1]
        val intersect = ((yi > py) != (yj > py)) && (px < (xj - xi) * (py - yi) / (yj - yi) + xi)
        if (intersect) inside = !inside
        j = i
    }
    return inside
}

class BoxTracker(
    val alpha: Double = 0.5,
    val resetAfterS: Double = 1.0,
    val tauS: Double = if (alpha > 0.0 && alpha < 1.0) -0.125 / ln(1.0 - alpha) else 0.0,
) {
    private var smoothed: Box? = null
    private var lastTs: Double? = null
    private var lastDogTs: Double? = null
    private var misses: Int = 0

    fun update(raw: Box?, ts: Double): Box? {
        val dt = if (lastTs != null) max(0.0, ts - lastTs!!) else 0.125
        if (raw == null) {
            misses++
            if (lastDogTs != null && (ts - lastDogTs!!) >= resetAfterS) {
                smoothed = null
            }
            lastTs = ts
            return null
        }

        if (lastDogTs != null && (ts - lastDogTs!!) >= resetAfterS) {
            smoothed = null
        }
        misses = 0
        lastDogTs = ts

        val prev = smoothed
        if (prev == null || boxIou(prev, raw) < 0.1) {
            smoothed = raw
        } else {
            val a = if (tauS > 0.0 && dt > 0.0) {
                1.0 - exp(-dt / tauS)
            } else {
                alpha
            }
            smoothed = Box(
                x1 = a * raw.x1 + (1.0 - a) * prev.x1,
                y1 = a * raw.y1 + (1.0 - a) * prev.y1,
                x2 = a * raw.x2 + (1.0 - a) * prev.x2,
                y2 = a * raw.y2 + (1.0 - a) * prev.y2,
            )
        }
        lastTs = ts
        return smoothed
    }

    fun current(): Box? = smoothed

    fun reset() {
        smoothed = null
        lastTs = null
        lastDogTs = null
        misses = 0
    }
}
