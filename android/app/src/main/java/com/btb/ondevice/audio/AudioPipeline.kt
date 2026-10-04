// Complete on-device audio perception pipeline
// Port of backend/audio/yamnet_events.py
// Commit: 3683457991777e5c22e573bdb472a70202ac5ddb
package com.btb.ondevice.audio

import com.btb.ondevice.contracts.AudioEvent

class AudioPipeline(
    val config: YamnetConfig = YamnetConfig(),
    val classifier: YamnetClassifier = YamnetClassifier(config),
) {
    private val buffer = AudioChunkBuffer(
        sampleRate = config.sampleRate,
        windowSize = config.windowSamples,
        hopSize = config.hopSamples,
    )
    private val debouncer = AudioEventDebouncer(
        debounceS = config.debounceS,
        silenceRms = config.silenceRms,
        threshold = config.threshold,
    )

    fun reset() {
        buffer.reset()
        debouncer.reset()
    }

    /**
     * Feed one chunk of PCM16 samples (e.g. from AudioRecord).
     * [ts] is the timestamp of the first sample in seconds.
     */
    fun pushPcm16(ts: Double, pcm16: ShortArray): List<AudioEvent> {
        val floats = FloatArray(pcm16.size)
        val scale = config.pcmScale.toFloat()
        for (i in pcm16.indices) {
            floats[i] = pcm16[i] / scale
        }
        return pushFloat(ts, floats)
    }

    /**
     * Feed one chunk of float32 samples.
     */
    fun pushFloat(ts: Double, chunk: FloatArray): List<AudioEvent> {
        val windows = buffer.push(ts, chunk)
        val events = mutableListOf<AudioEvent>()

        for ((windowEndTs, window) in windows) {
            val silence = debouncer.checkSilence(window)
            val (label, score) = if (silence != null) {
                silence
            } else {
                classifier.classify(window)
            }

            val event = debouncer.processWindow(windowEndTs, label, score)
            if (event != null) {
                events.add(event)
            }
        }

        return events
    }
}
