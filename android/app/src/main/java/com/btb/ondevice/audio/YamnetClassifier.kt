// Port of backend/vision/mobile_runners.py (MobileAudio) and backend/audio/yamnet_events.py
// Commit: 3683457991777e5c22e573bdb472a70202ac5ddb
package com.btb.ondevice.audio

import com.btb.ondevice.contracts.AudioLabel
import com.btb.ondevice.ml.ModelRunner
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.math.max
import kotlin.math.min

data class YamnetConfig(
    val windowSamples: Int = 15600,
    val sampleRate: Int = 16000,
    val hopSamples: Int = 7680,
    val threshold: Double = 0.3,
    val silenceRms: Double = 0.005,
    val debounceS: Double = 1.0,
    val numClasses: Int = 521,
    val pcmScale: Double = 32768.0,
    val dogClassMap: Map<AudioLabel, List<Int>> = mapOf(
        AudioLabel.bark to listOf(70, 73),
        AudioLabel.yip to listOf(71),
        AudioLabel.howl to listOf(72),
        AudioLabel.growl to listOf(74),
        AudioLabel.whimper to listOf(75),
    ),
)

open class YamnetClassifier(
    val config: YamnetConfig = YamnetConfig(),
    private val runner: ModelRunner? = null,
) {
    private val otherIndices: List<Int> by lazy {
        val mapped = config.dogClassMap.values.flatten().toSet()
        (0 until config.numClasses).filter { it !in mapped }
    }

    private val inputBuffer: ByteBuffer by lazy {
        ByteBuffer.allocateDirect(config.windowSamples * 4).order(ByteOrder.nativeOrder())
    }

    private val outputBuffer: ByteBuffer by lazy {
        ByteBuffer.allocateDirect(config.numClasses * 4).order(ByteOrder.nativeOrder())
    }

    /**
     * Score a flat array of 521 class probabilities into (AudioLabel, Double).
     */
    fun scoreClasses(scores: FloatArray): Pair<AudioLabel, Double> {
        require(scores.size >= config.numClasses) {
            "Expected at least ${config.numClasses} scores, got ${scores.size}"
        }

        var bestDogLabel: AudioLabel? = null
        var bestDogScore = -1.0

        for ((label, indices) in config.dogClassMap) {
            var labelMax = -1.0
            for (idx in indices) {
                val s = scores[idx].toDouble()
                if (s > labelMax) labelMax = s
            }
            if (labelMax > bestDogScore) {
                bestDogScore = labelMax
                bestDogLabel = label
            }
        }

        if (bestDogLabel != null && bestDogScore >= config.threshold) {
            return bestDogLabel to max(0.0, min(1.0, bestDogScore))
        }

        var otherMax = 0.0
        for (idx in otherIndices) {
            val s = scores[idx].toDouble()
            if (s > otherMax) otherMax = s
        }

        return AudioLabel.other to max(0.0, min(1.0, otherMax))
    }

    /**
     * Classify a 15,600 float sample window using the ModelRunner.
     */
    open fun classify(window: FloatArray): Pair<AudioLabel, Double> {
        val modelRunner = runner ?: return AudioLabel.other to 0.0

        inputBuffer.clear()
        val floatIn = inputBuffer.asFloatBuffer()
        val count = min(window.size, config.windowSamples)
        floatIn.put(window, 0, count)
        // Pad with zeros if short
        for (i in count until config.windowSamples) {
            floatIn.put(0.0f)
        }

        outputBuffer.clear()
        modelRunner.run(arrayOf(inputBuffer), arrayOf(outputBuffer))

        val scores = FloatArray(config.numClasses)
        outputBuffer.rewind()
        outputBuffer.asFloatBuffer().get(scores)

        return scoreClasses(scores)
    }

    /**
     * Classify a 15,600 PCM16 sample window.
     */
    fun classifyPcm16(pcm: ShortArray): Pair<AudioLabel, Double> {
        val floats = FloatArray(pcm.size)
        val scale = config.pcmScale.toFloat()
        for (i in pcm.indices) {
            floats[i] = pcm[i] / scale
        }
        return classify(floats)
    }
}
