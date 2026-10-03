package com.btb.ondevice.pipeline

import kotlin.math.abs
import kotlin.math.sqrt

/**
 * Result of Tier-0 activity gating.
 *
 * @param visionActive True if frame difference exceeds [diffThreshold], or first frame.
 * @param audioActive True if audio RMS exceeds [silenceRms].
 * @param frameDiff Normalized mean absolute difference in [0.0, 1.0].
 * @param audioRms Root-mean-square audio energy in [0.0, 1.0].
 */
data class TierZeroResult(
    val visionActive: Boolean,
    val audioActive: Boolean,
    val frameDiff: Double,
    val audioRms: Double,
) {
    /** True if either vision or audio has sufficient activity to process downstream models. */
    val anyActive: Boolean get() = visionActive || audioActive
}

/**
 * Ultra-cheap activity gate (Tier-0) executed before expensive neural network models.
 *
 * Checks:
 *  1. Vision: Mean absolute difference between consecutive downsampled Y-plane frames.
 *  2. Audio: RMS energy of incoming PCM buffer.
 *
 * When both are idle/silent, neural network inference (YOLO, RTMPose, Face, YAMNet)
 * can be skipped to preserve battery and thermal headroom on low-end devices.
 */
class TierZeroGate(
    val enabled: Boolean = true,
    val diffThreshold: Double = 0.02,
    val silenceRms: Double = 0.005,
    val minDogMotion: Double = 0.01,
) {
    private var prevFrame: ByteArray? = null

    fun reset() {
        prevFrame = null
    }

    /**
     * Compute normalized mean absolute difference between current and previous grayscale Y-plane.
     * Normalized to [0.0, 1.0] (where 1.0 represents a jump from black to white across all pixels).
     */
    fun computeFrameDiff(currentFrame: ByteArray): Double {
        val prev = prevFrame
        if (prev == null || prev.size != currentFrame.size) {
            prevFrame = currentFrame.copyOf()
            return 1.0 // First frame is always considered active
        }

        var sumDiff = 0L
        val n = currentFrame.size
        for (i in 0 until n) {
            val currVal = currentFrame[i].toInt() and 0xFF
            val prevVal = prev[i].toInt() and 0xFF
            sumDiff += abs(currVal - prevVal)
        }
        System.arraycopy(currentFrame, 0, prev, 0, n)
        return (sumDiff.toDouble() / n) / 255.0
    }

    /**
     * Compute RMS of float audio samples in [-1.0, 1.0].
     */
    fun computeAudioRms(samples: FloatArray, offset: Int = 0, length: Int = samples.size): Double {
        if (length <= 0) return 0.0
        var sumSq = 0.0
        for (i in offset until (offset + length)) {
            val v = samples[i].toDouble()
            sumSq += v * v
        }
        return sqrt(sumSq / length)
    }

    /**
     * Compute RMS of 16-bit PCM audio samples.
     */
    fun computeAudioRmsPcm16(pcm16: ShortArray, offset: Int = 0, length: Int = pcm16.size): Double {
        if (length <= 0) return 0.0
        var sumSq = 0.0
        for (i in offset until (offset + length)) {
            val v = pcm16[i].toDouble() / 32768.0
            sumSq += v * v
        }
        return sqrt(sumSq / length)
    }

    /**
     * Evaluate gating for current video frame and/or audio chunk.
     */
    fun evaluate(
        currentFrame: ByteArray?,
        audioSamples: FloatArray? = null,
    ): TierZeroResult {
        if (!enabled) {
            return TierZeroResult(
                visionActive = true,
                audioActive = true,
                frameDiff = 1.0,
                audioRms = 1.0,
            )
        }

        val diff = if (currentFrame != null) computeFrameDiff(currentFrame) else 0.0
        val rms = if (audioSamples != null) computeAudioRms(audioSamples) else 0.0

        val visionActive = currentFrame == null || diff >= diffThreshold
        val audioActive = audioSamples != null && rms >= silenceRms

        return TierZeroResult(
            visionActive = visionActive,
            audioActive = audioActive,
            frameDiff = diff,
            audioRms = rms,
        )
    }
}
