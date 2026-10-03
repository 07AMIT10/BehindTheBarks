// Port of backend/vision/features.py
// Commit: 1a5ddb32e6aae3b565a69915090c0d05120065ca
package com.btb.ondevice.vision

import com.btb.ondevice.config.DataConfig
import com.btb.ondevice.contracts.EarPosition
import com.btb.ondevice.contracts.Features
import com.btb.ondevice.contracts.FrameEvent
import kotlin.math.abs
import kotlin.math.atan2
import kotlin.math.ceil
import kotlin.math.cos
import kotlin.math.exp
import kotlin.math.ln
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sin
import kotlin.math.sqrt

data class Pt2(val x: Double, val y: Double) {
    operator fun plus(other: Pt2): Pt2 = Pt2(x + other.x, y + other.y)
    operator fun minus(other: Pt2): Pt2 = Pt2(x - other.x, y - other.y)
    operator fun times(scalar: Double): Pt2 = Pt2(x * scalar, y * scalar)
    operator fun div(scalar: Double): Pt2 = Pt2(x / scalar, y / scalar)
    fun dot(other: Pt2): Double = x * other.x + y * other.y
    fun norm(): Double = sqrt(x * x + y * y)
}

class FeatureEma(val tauS: Double) {
    var value: Double? = null
    var lastTs: Double? = null

    fun step(x: Double?, ts: Double, resetGap: Double): Double? {
        if (x == null) return null
        val prevVal = value
        val prevTs = lastTs
        if (prevVal == null || prevTs == null || (ts - prevTs) > resetGap) {
            value = x
        } else {
            val dt = max(0.0, ts - prevTs)
            val alpha = if (tauS > 0.0 && dt > 0.0) 1.0 - exp(-dt / tauS) else 1.0
            value = alpha * x + (1.0 - alpha) * prevVal
        }
        lastTs = ts
        return value
    }
}

class FeatureExtractor(
    val config: DataConfig = DataConfig(),
    val source: String = "live",
) {
    private data class Sample(
        val ts: Double,
        val pts: Map<String, Pt2>,
        val bbox: List<Double>?,
    )

    private val window = mutableListOf<Sample>()
    private val heights = mutableListOf<Pair<Double, Double>>()
    private val earVotes = mutableListOf<Pair<Double, String>>()
    private var lastDogTs: Double? = null

    private val smoothEmas = mutableMapOf<String, FeatureEma>()

    init {
        val smoothConfig = config.features.smooth
        for ((k, alpha) in smoothConfig) {
            val tau = if (alpha >= 1.0) 0.0 else if (alpha <= 0.0) Double.POSITIVE_INFINITY else -(1.0 / 8.0) / ln(1.0 - alpha)
            smoothEmas[k] = FeatureEma(tau)
        }
    }

    fun reset() {
        window.clear()
        heights.clear()
        earVotes.clear()
        lastDogTs = null
        for (e in smoothEmas.values) {
            e.value = null
            e.lastTs = null
        }
    }

    fun update(
        ts: Double,
        bbox: List<Double>?,
        bboxConf: Double?,
        inFeedingZone: Boolean,
        kps: Map<String, List<Double>?>?,
        lms: List<List<Double>>?,
        roiWagHz: Double? = null,
    ): FrameEvent {
        if (bbox == null) {
            if (lastDogTs != null && (ts - lastDogTs!!) > config.features.gapResetS) {
                window.clear()
                earVotes.clear()
                for (e in smoothEmas.values) {
                    e.value = null
                    e.lastTs = null
                }
            }
            trim(ts)
            return FrameEvent(
                ts = ts,
                source = source,
                dogDetected = false,
                bbox = null,
                bboxConf = null,
                bodyKeypoints = emptyMap(),
                faceLandmarks = null,
                features = Features(inFeedingZone = false),
            )
        }

        lastDogTs = ts
        val confThr = config.keypointConfThreshold
        val pts = mutableMapOf<String, Pt2>()
        if (kps != null) {
            for ((n, k) in kps) {
                if (k != null && k.size >= 3 && k[2] >= confThr) {
                    pts[n] = Pt2(k[0], k[1])
                }
            }
        }

        window.add(Sample(ts, pts, bbox))
        trim(ts)
        val scale = computeBodyScale()

        val rawHeight = computeTailHeight(pts, scale)
        val poseWag = computeTailWagHz(scale)
        val wagSource = config.features.wag.source
        val wagHz = when (wagSource) {
            "pose" -> poseWag
            "roi" -> roiWagHz
            "both" -> roiWagHz ?: poseWag
            else -> poseWag
        }

        val features = Features(
            tailHeight = smoothed("tail_height", rawHeight, ts, -1.0, 1.0),
            tailWagHz = wagHz,
            earPosition = computeEarPosition(ts, pts, lms, scale),
            mouthOpen = smoothed("mouth_open", computeMouthOpen(lms), ts, 0.0, 1.0),
            bodyLowering = smoothed("body_lowering", computeBodyLowering(ts, pts, bbox, scale), ts, 0.0, 1.0),
            motionEnergy = smoothed("motion_energy", computeMotionEnergy(scale), ts, 0.0, 1.0),
            inFeedingZone = inFeedingZone,
        )

        val cleanKps = mutableMapOf<String, List<Double>?>()
        if (kps != null) {
            for ((n, k) in kps) {
                cleanKps[n] = if (k != null && k.size >= 3 && k[2] >= confThr) k else null
            }
        }

        return FrameEvent(
            ts = ts,
            source = source,
            dogDetected = true,
            bbox = bbox,
            bboxConf = if (bboxConf != null) min(max(bboxConf, 0.0), 1.0) else null,
            bodyKeypoints = cleanKps,
            faceLandmarks = lms,
            features = features,
        )
    }

    private fun trim(ts: Double) {
        val winS = config.features.windowS
        while (window.isNotEmpty() && (ts - window.first().ts) > winS) {
            window.removeAt(0)
        }
        val baseS = config.features.baselineS
        while (heights.isNotEmpty() && (ts - heights.first().first) > baseS) {
            heights.removeAt(0)
        }
        val earVoteS = 1.0
        while (earVotes.isNotEmpty() && (ts - earVotes.first().first) > earVoteS) {
            earVotes.removeAt(0)
        }
    }

    private fun smoothed(name: String, x: Double?, ts: Double, lo: Double, hi: Double): Double? {
        val ema = smoothEmas[name] ?: return x?.let { min(max(it, lo), hi) }
        val y = ema.step(x, ts, config.features.windowS) ?: return null
        return min(max(y, lo), hi)
    }

    private fun computeBodyScale(): Double? {
        val lens = mutableListOf<Double>()
        for (w in window) {
            val withers = w.pts["withers"]
            val hip = w.pts["hip"]
            if (withers != null && hip != null) {
                val d = (withers - hip).norm()
                if (d > 1.0) lens.add(d)
            }
        }
        if (lens.isNotEmpty()) {
            return WagEstimatorMath.median(lens)
        }
        val sides = mutableListOf<Double>()
        for (w in window) {
            val b = w.bbox ?: continue
            val bw = b[2] - b[0]
            val bh = b[3] - b[1]
            sides.add(max(bw, bh))
        }
        return if (sides.isNotEmpty()) WagEstimatorMath.median(sides) * 0.8 else null
    }

    private fun computeTailHeight(pts: Map<String, Pt2>, scale: Double?): Double? {
        if (scale == null) return null
        val withers = pts["withers"] ?: return null
        val tailBase = pts["tail_base"] ?: return null
        val tailTip = pts["tail_tip"] ?: return null
        val end = pts["hip"] ?: tailBase
        val back = end - withers
        val norm = back.norm()
        if (norm < 1e-6) return null
        val backNorm = back / norm
        var up = Pt2(backNorm.y, -backNorm.x)
        if (up.y > 0.0) {
            up = Pt2(-up.x, -up.y)
        }
        if (abs(up.y) < 0.3) return null
        val h = (tailTip - tailBase).dot(up) / (scale * config.features.tailHeightScale)
        return min(max(h, -1.0), 1.0)
    }

    private fun computeTailWagHz(scale: Double?): Double? {
        if (scale == null) return null
        val rows = mutableListOf<Pair<Double, Pt2>>()
        for (w in window) {
            val tip = w.pts["tail_tip"]
            val base = w.pts["tail_base"]
            if (tip != null && base != null) {
                rows.add(Pair(w.ts, tip - base))
            }
        }
        if (rows.size < 10) return null

        val run = mutableListOf(rows.last())
        for (i in rows.size - 2 downTo 0) {
            if (run.last().first - rows[i].first > 0.75) break
            run.add(rows[i])
        }
        run.reverse()
        if (run.size < 10 || (run.last().first - run.first().first) < 1.5) return null

        val t = run.map { it.first }
        val xy = run.map { it.second / scale }
        val meanX = xy.map { it.x }.average()
        val meanY = xy.map { it.y }.average()
        val centered = xy.map { Pt2(it.x - meanX, it.y - meanY) }

        var cxx = 0.0
        var cyy = 0.0
        var cxy = 0.0
        for (p in centered) {
            cxx += p.x * p.x
            cyy += p.y * p.y
            cxy += p.x * p.y
        }
        val theta = 0.5 * atan2(2.0 * cxy, cxx - cyy)
        val axis = Pt2(cos(theta), sin(theta))
        val sig = centered.map { it.dot(axis) }

        return estimateWagHz(
            t = t,
            signal = sig,
            minHz = config.features.wag.minHz,
            maxHz = config.features.wag.maxHz,
            minStd = config.features.wagMinStd,
            minPeakRatio = config.features.wagMinPeakRatio,
            minSamples = 10,
            minSpanS = 1.5,
            maxGapS = 0.75,
        )
    }

    private fun computeEarPosition(
        ts: Double,
        pts: Map<String, Pt2>,
        lms: List<List<Double>>?,
        scale: Double?,
    ): EarPosition {
        if (scale == null) return EarPosition.unknown
        val earSides = listOf(
            Triple("left_ear_base", "left_ear_tip", Pair(listOf(0, 12), 6)),
            Triple("right_ear_base", "right_ear_tip", Pair(listOf(1, 13), 7)),
        )
        val perEar = mutableListOf<String>()

        for ((baseN, tipN, lmsIdx) in earSides) {
            val nose: Pt2
            val eyes: Pt2
            val base: Pt2
            val tip: Pt2

            if (lms != null && lms.size >= 46) {
                var nx = 0.0
                var ny = 0.0
                for (idx in 24..35) {
                    nx += lms[idx][0]
                    ny += lms[idx][1]
                }
                nose = Pt2(nx / 12.0, ny / 12.0)

                val eyeIndices = listOf(16, 17, 18, 19, 20, 21, 22, 23)
                var ex = 0.0
                var ey = 0.0
                for (idx in eyeIndices) {
                    ex += lms[idx][0]
                    ey += lms[idx][1]
                }
                eyes = Pt2(ex / 8.0, ey / 8.0)

                var bx = 0.0
                var by = 0.0
                for (idx in lmsIdx.first) {
                    bx += lms[idx][0]
                    by += lms[idx][1]
                }
                base = Pt2(bx / lmsIdx.first.size.toDouble(), by / lmsIdx.first.size.toDouble())
                tip = Pt2(lms[lmsIdx.second][0], lms[lmsIdx.second][1])
            } else if (pts.containsKey("nose") && pts.containsKey(baseN) && pts.containsKey(tipN) &&
                (pts.containsKey("left_eye") || pts.containsKey("right_eye"))
            ) {
                nose = pts["nose"]!!
                val eyePts = listOfNotNull(pts["left_eye"], pts["right_eye"])
                eyes = Pt2(eyePts.map { it.x }.average(), eyePts.map { it.y }.average())
                base = pts[baseN]!!
                tip = pts[tipN]!!
            } else {
                continue
            }

            val fwd = nose - eyes
            val ear = tip - base
            val fl = fwd.norm()
            val el = ear.norm()
            if (fl < 0.03 * scale || el < 0.03 * scale) continue
            val fwdNorm = fwd / fl
            val earNorm = ear / el

            val upCos = -earNorm.y
            if (upCos >= config.features.earUpMin) {
                perEar.add("up")
            } else if (earNorm.dot(Pt2(-fwdNorm.x, -fwdNorm.y)) >= config.features.earBackCos && abs(fwdNorm.y) < 0.7) {
                perEar.add("back")
            } else {
                perEar.add("neutral")
            }
        }

        if (perEar.isNotEmpty()) {
            val vote = if (perEar.distinct().size == 1) perEar[0] else "neutral"
            earVotes.add(Pair(ts, vote))
        }

        if (earVotes.isEmpty()) return EarPosition.unknown
        val counts = mutableMapOf<String, Int>()
        for ((_, v) in earVotes) {
            counts[v] = (counts[v] ?: 0) + 1
        }
        val winner = counts.maxByOrNull { it.value }?.key ?: "unknown"
        return when (winner) {
            "up" -> EarPosition.up
            "neutral" -> EarPosition.neutral
            "back" -> EarPosition.back
            else -> EarPosition.unknown
        }
    }

    private fun computeMouthOpen(lms: List<List<Double>>?): Double? {
        if (lms == null || lms.size < 46) return null
        var minX = Double.POSITIVE_INFINITY
        var maxX = Double.NEGATIVE_INFINITY
        var minY = Double.POSITIVE_INFINITY
        var maxY = Double.NEGATIVE_INFINITY
        for (idx in 16..35) {
            val p = lms[idx]
            if (p[0] < minX) minX = p[0]
            if (p[0] > maxX) maxX = p[0]
            if (p[1] < minY) minY = p[1]
            if (p[1] > maxY) maxY = p[1]
        }
        val size = sqrt((maxX - minX) * (maxX - minX) + (maxY - minY) * (maxY - minY))
        if (size < 1e-6) return null

        val p38 = lms[38]
        val p41 = lms[41]
        val gap = sqrt((p38[0] - p41[0]) * (p38[0] - p41[0]) + (p38[1] - p41[1]) * (p38[1] - p41[1]))
        val ratio = gap / size
        val lo = config.features.mouthClosed
        val hi = config.features.mouthFull
        return min(max((ratio - lo) / (hi - lo), 0.0), 1.0)
    }

    private fun computeBodyLowering(
        ts: Double,
        pts: Map<String, Pt2>,
        bbox: List<Double>?,
        scale: Double?,
    ): Double? {
        val ys = listOfNotNull(pts["withers"]?.y, pts["hip"]?.y)
        if (bbox != null && ys.isNotEmpty() && scale != null) {
            val currentH = (bbox[3] - ys.average()) / scale
            heights.add(Pair(ts, currentH))
        }
        if (heights.size < 2 || (heights.last().first - heights.first().first) < 2.0) {
            return null
        }
        val recent = heights.filter { (ts - it.first) <= 1.0 }.map { it.second }
        if (recent.isEmpty()) return null

        val allHeights = heights.map { it.second }.sorted()
        val pIdx = ((allHeights.size - 1) * 0.9).toInt()
        val base = allHeights[pIdx]
        if (base <= 0.05) return null

        val drop = (base - WagEstimatorMath.median(recent)) / base
        val deadband = config.features.loweringDeadband
        val full = config.features.loweringFull
        return min(max((drop - deadband) / (full - deadband), 0.0), 1.0)
    }

    private fun computeMotionEnergy(scale: Double?): Double? {
        if (scale == null || window.size < 2) return null
        val tStart = window.first().ts
        val tEnd = window.last().ts
        val gridDt = 0.2
        if ((tEnd - tStart) < gridDt) return null

        val gridTimes = mutableListOf<Double>()
        var i = 0
        while (tStart + i * gridDt < tEnd + 1e-6) {
            gridTimes.add(tStart + i * gridDt)
            i++
        }
        if (gridTimes.size < 2) return null

        val motionKps = listOf("nose", "withers", "hip", "left_front_paw", "right_front_paw", "left_back_paw", "right_back_paw")
        val gridPts = mutableListOf<Map<String, Pt2>>()
        var wIdx = 0
        val wLen = window.size

        for (gt in gridTimes) {
            while (wIdx < wLen - 2 && window[wIdx + 1].ts < gt) {
                wIdx++
            }
            val a = window[wIdx]
            val b = window[wIdx + 1]
            val dt = b.ts - a.ts
            if (dt > 1.0 || dt <= 0.0) {
                gridPts.add(emptyMap())
                continue
            }
            val frac = min(max((gt - a.ts) / dt, 0.0), 1.0)
            val ptsAtG = mutableMapOf<String, Pt2>()
            for (n in motionKps) {
                val pa = a.pts[n]
                val pb = b.pts[n]
                if (pa != null && pb != null) {
                    ptsAtG[n] = Pt2(
                        (1.0 - frac) * pa.x + frac * pb.x,
                        (1.0 - frac) * pa.y + frac * pb.y,
                    )
                }
            }
            gridPts.add(ptsAtG)
        }

        val speeds = mutableListOf<Double>()
        for (i in 0 until gridPts.size - 1) {
            val g0 = gridPts[i]
            val g1 = gridPts[i + 1]
            if (g0.isEmpty() || g1.isEmpty()) continue
            val common = motionKps.filter { it in g0 && it in g1 }
            if (common.size < 2) continue
            val kpDistances = common.map { n -> (g1[n]!! - g0[n]!!).norm() }
            val medianDist = WagEstimatorMath.median(kpDistances)
            speeds.add(medianDist / gridDt / scale)
        }

        if (speeds.size < 2) return null
        val meanSpeed = speeds.average()
        val floor = config.features.motionFloor
        val full = config.features.motionFull
        return min(max((meanSpeed - floor) / (full - floor), 0.0), 1.0)
    }
}
