// Port of backend/audio/yamnet_events.py
// Commit: 3683457991777e5c22e573bdb472a70202ac5ddb
package com.btb.ondevice.audio

import com.btb.ondevice.config.AudioConfig
import com.btb.ondevice.contracts.AudioEvent
import com.btb.ondevice.contracts.AudioLabel
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sqrt

/**
 * Manages buffering raw 16 kHz audio chunks into overlapping YAMNet windows.
 *
 * YAMNet requires 15,600 samples (0.975 s) per inference window.
 * Default hop is 7,680 samples (0.48 s).
 */
class AudioChunkBuffer(
    val sampleRate: Int = 16000,
    val windowSize: Int = 15600,
    val hopSize: Int = 7680,
    val maxGapS: Double = 0.5,
) {
    private var buf = FloatArray(0)
    private var bufTs = 0.0

    fun reset() {
        buf = FloatArray(0)
        bufTs = 0.0
    }

    /**
     * Push a chunk of float32 samples where [ts] is the timestamp of the first sample.
     * Returns a list of (windowEndTs, windowSamples).
     */
    fun push(ts: Double, chunk: FloatArray): List<Pair<Double, FloatArray>> {
        if (buf.isNotEmpty()) {
            val expected = bufTs + buf.size.toDouble() / sampleRate
            if (abs(ts - expected) > maxGapS) {
                buf = FloatArray(0)
            }
        }
        if (buf.isEmpty()) {
            bufTs = ts
        }
        val newBuf = FloatArray(buf.size + chunk.size)
        System.arraycopy(buf, 0, newBuf, 0, buf.size)
        System.arraycopy(chunk, 0, newBuf, buf.size, chunk.size)
        buf = newBuf

        val windows = mutableListOf<Pair<Double, FloatArray>>()
        while (buf.size >= windowSize) {
            val window = FloatArray(windowSize)
            System.arraycopy(buf, 0, window, 0, windowSize)
            val windowEndTs = bufTs + windowSize.toDouble() / sampleRate
            windows.add(windowEndTs to window)

            val remaining = buf.size - hopSize
            val nextBuf = FloatArray(remaining)
            System.arraycopy(buf, hopSize, nextBuf, 0, remaining)
            buf = nextBuf
            bufTs += hopSize.toDouble() / sampleRate
        }
        return windows
    }
}

/**
 * Debounces classified audio windows according to YAMNet audio event rules:
 *
 * 1. Dog labels (bark, yip, growl, whimper, howl) are emitted at most once per [debounceS].
 * 2. Silence / other labels are emitted only when they differ from the previous window label.
 * 3. When [debounceS] <= 0.0, every window is emitted.
 */
class AudioEventDebouncer(
    val debounceS: Double = 1.0,
    val silenceRms: Double = 0.005,
    val threshold: Double = 0.3,
) {
    companion object {
        val DOG_LABELS = setOf(
            AudioLabel.bark,
            AudioLabel.yip,
            AudioLabel.growl,
            AudioLabel.whimper,
            AudioLabel.howl,
        )

        fun computeRms(samples: FloatArray, offset: Int = 0, length: Int = samples.size): Double {
            if (length <= 0) return 0.0
            var sumSq = 0.0
            for (i in offset until (offset + length)) {
                val v = samples[i].toDouble()
                sumSq += v * v
            }
            return sqrt(sumSq / length)
        }
    }

    constructor(config: AudioConfig) : this(
        debounceS = config.debounceS,
        silenceRms = config.silenceRms,
        threshold = config.threshold,
    )

    private var lastWindowLabel: AudioLabel? = null
    private val lastEmit = mutableMapOf<AudioLabel, Double>()

    fun reset() {
        lastWindowLabel = null
        lastEmit.clear()
    }

    /**
     * Feed one scored window. Returns an [AudioEvent] if it passes debouncing, or null if suppressed.
     *
     * @param ts Timestamp at the END of the window (when the sound became knowable).
     * @param label Window label.
     * @param score Classification confidence or silence score.
     */
    fun processWindow(ts: Double, label: AudioLabel, score: Double): AudioEvent? {
        val prev = lastWindowLabel
        lastWindowLabel = label
        if (label in DOG_LABELS) {
            val last = lastEmit[label]
            if (last != null && ts - last < debounceS) {
                return null
            }
            lastEmit[label] = ts
        } else if (label == prev && debounceS > 0.0) {
            return null
        }
        return AudioEvent(ts = ts, label = label, score = score)
    }

    /**
     * Checks if a window is below the silence RMS threshold without needing model execution.
     * Returns Pair(AudioLabel.silence, score) if silent, or null if non-silent.
     */
    fun checkSilence(window: FloatArray, offset: Int = 0, length: Int = window.size): Pair<AudioLabel, Double>? {
        val rms = computeRms(window, offset, length)
        if (rms < silenceRms) {
            val score = max(0.0, min(1.0, 1.0 - rms / silenceRms))
            return AudioLabel.silence to score
        }
        return null
    }
}
