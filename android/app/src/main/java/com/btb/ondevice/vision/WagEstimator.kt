// Port of backend/vision/wag.py
// Commit: 1a5ddb32e6aae3b565a69915090c0d05120065ca
package com.btb.ondevice.vision

import kotlin.math.abs
import kotlin.math.ceil
import kotlin.math.cos
import kotlin.math.ln
import kotlin.math.log2
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sin
import kotlin.math.sqrt

object WagEstimatorMath {
    /**
     * In-place Radix-2 Cooley-Tukey FFT. n must be a power of 2.
     */
    fun fftRadix2(re: DoubleArray, im: DoubleArray) {
        val n = re.size
        var j = 0
        for (i in 0 until n - 1) {
            if (i < j) {
                val tempR = re[i]; re[i] = re[j]; re[j] = tempR
                val tempI = im[i]; im[i] = im[j]; im[j] = tempI
            }
            var k = n shr 1
            while (k <= j) {
                j -= k
                k = k shr 1
            }
            j += k
        }
        var len = 2
        while (len <= n) {
            val half = len shr 1
            val angle = -2.0 * Math.PI / len
            val wStepR = cos(angle)
            val wStepI = sin(angle)
            var i = 0
            while (i < n) {
                var wR = 1.0
                var wI = 0.0
                for (k in 0 until half) {
                    val uR = re[i + k]
                    val uI = im[i + k]
                    val vR = re[i + k + half] * wR - im[i + k + half] * wI
                    val vI = re[i + k + half] * wI + im[i + k + half] * wR
                    re[i + k] = uR + vR
                    im[i + k] = uI + vI
                    re[i + k + half] = uR - vR
                    im[i + k + half] = uI - vI
                    val nextWR = wR * wStepR - wI * wStepI
                    val nextWI = wR * wStepI + wI * wStepR
                    wR = nextWR
                    wI = nextWI
                }
                i += len
            }
            len = len shl 1
        }
    }

    fun median(values: List<Double>): Double {
        if (values.isEmpty()) return 0.0
        val sorted = values.sorted()
        val n = sorted.size
        return if (n % 2 == 1) {
            sorted[n / 2]
        } else {
            (sorted[n / 2 - 1] + sorted[n / 2]) / 2.0
        }
    }

    fun interp(grid: DoubleArray, xp: List<Double>, yp: List<Double>): DoubleArray {
        val out = DoubleArray(grid.size)
        var j = 0
        val n = xp.size
        for (k in grid.indices) {
            val x = grid[k]
            if (x <= xp[0]) {
                out[k] = yp[0]
            } else if (x >= xp[n - 1]) {
                out[k] = yp[n - 1]
            } else {
                while (j < n - 2 && xp[j + 1] < x) {
                    j++
                }
                val x0 = xp[j]
                val x1 = xp[j + 1]
                val y0 = yp[j]
                val y1 = yp[j + 1]
                val frac = if (x1 > x0) (x - x0) / (x1 - x0) else 0.0
                out[k] = y0 + frac * (y1 - y0)
            }
        }
        return out
    }
}

fun estimateWagHz(
    t: List<Double>,
    signal: List<Double>,
    minHz: Double = 1.0,
    maxHz: Double = 8.0,
    minStd: Double = 0.015,
    minPeakRatio: Double = 0.3,
    minSamples: Int = 10,
    minSpanS: Double = 1.5,
    maxGapS: Double = 0.75,
): Double? {
    if (t.size < minSamples || signal.size < minSamples) return null

    // Extract the latest unbroken run (gaps <= maxGapS)
    val runT = mutableListOf(t.last())
    val runSig = mutableListOf(signal.last())
    for (i in t.size - 2 downTo 0) {
        if (runT.last() - t[i] > maxGapS) break
        runT.add(t[i])
        runSig.add(signal[i])
    }
    runT.reverse()
    runSig.reverse()

    if (runT.size < minSamples || (runT.last() - runT.first()) < minSpanS) return null

    // Amplitude gate
    val meanSig = runSig.average()
    var variance = 0.0
    for (v in runSig) {
        variance += (v - meanSig) * (v - meanSig)
    }
    val std = sqrt(variance / runSig.size)
    if (std < minStd) return 0.0

    val diffs = mutableListOf<Double>()
    for (i in 0 until runT.size - 1) {
        diffs.add(runT[i + 1] - runT[i])
    }
    val dt = WagEstimatorMath.median(diffs)
    if (dt <= 0.0) return null

    // Resample onto uniform grid
    val t0 = runT.first()
    val tEnd = runT.last()
    val gridCount = max(1, ceil((tEnd - t0 + 1e-9) / dt).toInt() + 1)
    val grid = DoubleArray(gridCount) { i -> min(tEnd, t0 + i * dt) }
    val y = WagEstimatorMath.interp(grid, runT, runSig)

    // Detrend linear drift
    val n = y.size
    var meanX = 0.0
    var meanY = 0.0
    for (i in 0 until n) {
        val xVal = grid[i] - grid[0]
        meanX += xVal
        meanY += y[i]
    }
    meanX /= n
    meanY /= n

    var num = 0.0
    var den = 0.0
    for (i in 0 until n) {
        val xVal = (grid[i] - grid[0]) - meanX
        val yVal = y[i] - meanY
        num += xVal * yVal
        den += xVal * xVal
    }
    val slope = if (den > 0.0) num / den else 0.0
    val intercept = meanY - slope * meanX

    for (i in 0 until n) {
        val xVal = grid[i] - grid[0]
        y[i] -= (slope * xVal + intercept)
    }

    // FFT with Hann window
    val minPow = max(9, ceil(log2(n * 8.0)).toInt())
    val nfft = 1 shl minPow

    val re = DoubleArray(nfft)
    val im = DoubleArray(nfft)
    for (i in 0 until n) {
        val hann = 0.5 * (1.0 - cos(2.0 * Math.PI * i / (n - 1).toDouble()))
        re[i] = y[i] * hann
    }

    WagEstimatorMath.fftRadix2(re, im)

    val halfBins = nfft / 2 + 1
    val power = DoubleArray(halfBins)
    val freqs = DoubleArray(halfBins)
    val df = 1.0 / (nfft * dt)
    for (i in 0 until halfBins) {
        power[i] = re[i] * re[i] + im[i] * im[i]
        freqs[i] = i * df
    }

    val hi = min(maxHz, 0.95 * 0.5 / dt)
    val bandIndices = mutableListOf<Int>()
    for (i in 0 until halfBins) {
        if (freqs[i] >= minHz && freqs[i] <= hi) {
            bandIndices.add(i)
        }
    }
    if (bandIndices.size < 3) return null

    var maxPower = -1.0
    var bestK = -1
    for (idx in bandIndices) {
        if (power[idx] > maxPower) {
            maxPower = power[idx]
            bestK = idx
        }
    }
    if (bestK < 0) return null

    // Periodicity gate: power within +-0.25 Hz of peak / total power (freq >= 0.2)
    var nearPower = 0.0
    var totalPower = 0.0
    val peakFreq = freqs[bestK]
    for (i in 0 until halfBins) {
        if (freqs[i] >= 0.2) {
            totalPower += power[i]
            if (abs(freqs[i] - peakFreq) <= 0.25) {
                nearPower += power[i]
            }
        }
    }
    if (totalPower <= 0.0 || (nearPower / totalPower) < minPeakRatio) {
        return 0.0
    }

    // Parabolic log-interpolation around peak
    var f = freqs[bestK]
    if (bestK > 0 && bestK < halfBins - 1) {
        val a = ln(power[bestK - 1] + 1e-12)
        val b = ln(power[bestK] + 1e-12)
        val c = ln(power[bestK + 1] + 1e-12)
        val denom = a - 2.0 * b + c
        if (denom < 0.0) {
            f += 0.5 * (a - c) / denom * df
        }
    }

    return if (f >= minHz) f else 0.0
}

fun computeRoiShift(
    currRoi: Array<ByteArray>,
    prevRoi: Array<ByteArray>,
    boxDx: Double = 0.0,
): Pair<Double, Double> {
    val h = currRoi.size
    if (h == 0) return Pair(0.0, 0.0)
    val w = currRoi[0].size
    if (w == 0) return Pair(0.0, 0.0)

    var m00 = 0.0
    var m10 = 0.0

    for (y in 0 until h) {
        val currRow = currRoi[y]
        val prevRow = prevRoi[y]
        for (x in 0 until w) {
            val c = currRow[x].toInt() and 0xFF
            val p = prevRow[x].toInt() and 0xFF
            val diff = abs(c - p).toDouble()
            m00 += diff
            m10 += diff * x
        }
    }

    if (m00 < 1e-3) return Pair(0.0, 0.0)

    val centroidX = m10 / m00
    val centerX = (w - 1) / 2.0
    val shiftPx = centroidX - centerX - boxDx
    val shiftNorm = shiftPx / w
    return Pair(shiftNorm, m00)
}

class RoiWagEstimator(
    val minHz: Double = 1.0,
    val maxHz: Double = 8.0,
    val minStd: Double = 0.015,
    val wagMinPeakRatio: Double = 0.3,
    val windowS: Double = 3.0,
    val minSamples: Int = 10,
    val minSpanS: Double = 1.5,
    val maxGapS: Double = 0.75,
) {
    private val history = mutableListOf<Pair<Double, Double>>()
    private var prevRoi: Array<ByteArray>? = null
    var currentHz: Double? = null
        private set

    fun reset() {
        history.clear()
        prevRoi = null
        currentHz = null
    }

    fun push(roi: Array<ByteArray>, ts: Double, boxDx: Double = 0.0): Double? {
        val prev = prevRoi
        if (prev != null) {
            val (shift, _) = computeRoiShift(roi, prev, boxDx)
            history.add(Pair(ts, shift))
        }
        prevRoi = roi

        while (history.isNotEmpty() && (ts - history.first().first) > windowS) {
            history.removeAt(0)
        }

        currentHz = estimate()
        return currentHz
    }

    fun estimate(): Double? {
        if (history.size < minSamples) return null
        val t = history.map { it.first }
        val sig = history.map { it.second }
        return estimateWagHz(
            t = t,
            signal = sig,
            minHz = minHz,
            maxHz = maxHz,
            minStd = minStd,
            minPeakRatio = wagMinPeakRatio,
            minSamples = minSamples,
            minSpanS = minSpanS,
            maxGapS = maxGapS,
        )
    }
}
