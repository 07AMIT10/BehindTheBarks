package com.btb.ondevice.audio

import com.btb.ondevice.contracts.AudioEvent
import com.btb.ondevice.contracts.AudioLabel
import com.google.common.truth.Truth.assertThat
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.jupiter.api.Test
import java.io.InputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder

class AudioPipelineTest {

    private val json = Json { ignoreUnknownKeys = true }

    @Test
    fun `scoreClasses maps dog classes and other correctly`() {
        val classifier = YamnetClassifier()
        val scores = FloatArray(521)

        // Class 70 is Bark
        scores[70] = 0.85f
        val (barkLabel, barkScore) = classifier.scoreClasses(scores)
        assertThat(barkLabel).isEqualTo(AudioLabel.bark)
        assertThat(barkScore).isWithin(1e-4).of(0.85)

        // Reset and set non-dog class 10 to 0.75
        scores[70] = 0.0f
        scores[10] = 0.75f
        val (otherLabel, otherScore) = classifier.scoreClasses(scores)
        assertThat(otherLabel).isEqualTo(AudioLabel.other)
        assertThat(otherScore).isWithin(1e-4).of(0.75)

        // Sub-threshold dog class (0.25 < 0.3) with other class 0.5
        scores[70] = 0.25f
        scores[10] = 0.50f
        val (subDogLabel, subDogScore) = classifier.scoreClasses(scores)
        assertThat(subDogLabel).isEqualTo(AudioLabel.other)
        assertThat(subDogScore).isWithin(1e-4).of(0.50)
    }

    @Test
    fun `full replay of audio_5s_16k pcm fixture matches audio_events json exactly`() {
        // Load audio_windows.json
        val windowsJsonStream = javaClass.classLoader?.getResourceAsStream("audio/audio_windows.json")
            ?: error("Could not load audio/audio_windows.json")
        val windowsJson = json.parseToJsonElement(windowsJsonStream.bufferedReader().readText()).jsonArray

        // Map window index -> 521 scores
        val windowScores = mutableMapOf<Int, FloatArray>()
        for (elem in windowsJson) {
            val obj = elem.jsonObject
            val idx = obj["idx"]!!.jsonPrimitive.content.toInt()
            val arr = obj["scores"]!!.jsonArray
            val scores = FloatArray(arr.size) { arr[it].jsonPrimitive.content.toFloat() }
            windowScores[idx] = scores
        }

        // Custom classifier that looks up scores for the non-silent window sequence
        var nonSilentIndex = 1
        val fakeClassifier = object : YamnetClassifier() {
            override fun classify(window: FloatArray): Pair<AudioLabel, Double> {
                val scores = windowScores[nonSilentIndex] ?: FloatArray(521)
                nonSilentIndex++
                return scoreClasses(scores)
            }
        }

        val pipeline = AudioPipeline(classifier = fakeClassifier)

        // Load 5s PCM16 file
        val pcmStream: InputStream = javaClass.classLoader?.getResourceAsStream("audio/audio_5s_16k.pcm")
            ?: error("Could not load audio/audio_5s_16k.pcm")
        val pcmBytes = pcmStream.readBytes()
        assertThat(pcmBytes.size).isEqualTo(160000) // 5s * 16000 samples * 2 bytes

        val pcmShorts = ShortArray(pcmBytes.size / 2)
        ByteBuffer.wrap(pcmBytes).order(ByteOrder.LITTLE_ENDIAN).asShortBuffer().get(pcmShorts)

        // Push in 100 ms chunks (1600 samples)
        val chunkSize = 1600
        val sampleRate = 16000
        val emittedEvents = mutableListOf<AudioEvent>()

        var sampleOffset = 0
        while (sampleOffset < pcmShorts.size) {
            val count = minOf(chunkSize, pcmShorts.size - sampleOffset)
            val chunk = ShortArray(count)
            System.arraycopy(pcmShorts, sampleOffset, chunk, 0, count)
            val ts = sampleOffset.toDouble() / sampleRate

            val events = pipeline.pushPcm16(ts, chunk)
            emittedEvents.addAll(events)
            sampleOffset += count
        }

        // Verify emitted events match audio_events.json exactly
        val eventsJsonStream = javaClass.classLoader?.getResourceAsStream("audio/audio_events.json")
            ?: error("Could not load audio/audio_events.json")
        val expectedEvents = json.decodeFromString<List<AudioEvent>>(eventsJsonStream.bufferedReader().readText())

        assertThat(emittedEvents).hasSize(expectedEvents.size)
        for (i in expectedEvents.indices) {
            val actual = emittedEvents[i]
            val expected = expectedEvents[i]
            assertThat(actual.label).isEqualTo(expected.label)
            assertThat(actual.ts).isWithin(1e-3).of(expected.ts)
            assertThat(actual.score).isWithin(1e-4).of(expected.score)
        }
    }
}
