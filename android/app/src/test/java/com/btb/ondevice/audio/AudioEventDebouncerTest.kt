package com.btb.ondevice.audio

import com.btb.ondevice.contracts.AudioEvent
import com.btb.ondevice.contracts.AudioLabel
import com.btb.ondevice.contracts.ContractsJson
import com.google.common.truth.Truth.assertThat
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.decodeFromJsonElement
import kotlinx.serialization.json.double
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.jupiter.api.Test
import java.nio.ByteBuffer
import java.nio.ByteOrder

class AudioEventDebouncerTest {

    @Test
    fun `replaying audio_windows fixture through debouncer matches audio_events fixture`() {
        val windowsStream = javaClass.getResourceAsStream("/audio/audio_windows.json")
        assertThat(windowsStream).isNotNull()
        val windowsJson = windowsStream!!.bufferedReader().use { it.readText() }
        val windowsArr = Json.parseToJsonElement(windowsJson).jsonArray

        val eventsStream = javaClass.getResourceAsStream("/audio/audio_events.json")
        assertThat(eventsStream).isNotNull()
        val expectedEvents = ContractsJson.decodeFromString<List<AudioEvent>>(
            eventsStream!!.bufferedReader().use { it.readText() }
        )

        val debouncer = AudioEventDebouncer(debounceS = 1.0)
        val actualEvents = mutableListOf<AudioEvent>()

        for (el in windowsArr) {
            val obj = el.jsonObject
            val ts = obj["ts"]!!.jsonPrimitive.double
            val labelStr = obj["label"]!!.jsonPrimitive.content
            val score = obj["score"]!!.jsonPrimitive.double
            val label = AudioLabel.valueOf(labelStr)

            val ev = debouncer.processWindow(ts, label, score)
            if (ev != null) {
                actualEvents.add(ev)
            }
        }

        assertThat(actualEvents.size).isEqualTo(expectedEvents.size)
        for (i in expectedEvents.indices) {
            val act = actualEvents[i]
            val exp = expectedEvents[i]
            assertThat(act.ts).isWithin(1e-5).of(exp.ts)
            assertThat(act.label).isEqualTo(exp.label)
            assertThat(act.score).isWithin(1e-5).of(exp.score)
        }
    }

    @Test
    fun `dog labels are debounced individually by debounce_s`() {
        val debouncer = AudioEventDebouncer(debounceS = 1.0)

        // First bark: emitted
        val ev1 = debouncer.processWindow(1.0, AudioLabel.bark, 0.9)
        assertThat(ev1).isNotNull()
        assertThat(ev1!!.label).isEqualTo(AudioLabel.bark)

        // Second bark within 1.0s: suppressed
        val ev2 = debouncer.processWindow(1.5, AudioLabel.bark, 0.95)
        assertThat(ev2).isNull()

        // Yip within 1.0s of bark: emitted because it's a different dog label
        val ev3 = debouncer.processWindow(1.6, AudioLabel.yip, 0.85)
        assertThat(ev3).isNotNull()
        assertThat(ev3!!.label).isEqualTo(AudioLabel.yip)

        // Bark after 1.0s from first bark (ts 2.1 - 1.0 = 1.1s): emitted
        val ev4 = debouncer.processWindow(2.1, AudioLabel.bark, 0.88)
        assertThat(ev4).isNotNull()
        assertThat(ev4!!.label).isEqualTo(AudioLabel.bark)
    }

    @Test
    fun `non-dog labels are debounced on consecutive duplicates`() {
        val debouncer = AudioEventDebouncer(debounceS = 1.0)

        // Silence: emitted
        val ev1 = debouncer.processWindow(1.0, AudioLabel.silence, 1.0)
        assertThat(ev1).isNotNull()

        // Consecutive silence: suppressed
        val ev2 = debouncer.processWindow(1.5, AudioLabel.silence, 1.0)
        assertThat(ev2).isNull()

        // Label changes to other: emitted
        val ev3 = debouncer.processWindow(2.0, AudioLabel.other, 0.7)
        assertThat(ev3).isNotNull()

        // Consecutive other: suppressed
        val ev4 = debouncer.processWindow(2.5, AudioLabel.other, 0.8)
        assertThat(ev4).isNull()

        // Label changes back to silence: emitted
        val ev5 = debouncer.processWindow(3.0, AudioLabel.silence, 0.95)
        assertThat(ev5).isNotNull()
    }

    @Test
    fun `debounce_s 0 emits every window`() {
        val debouncer = AudioEventDebouncer(debounceS = 0.0)

        val ev1 = debouncer.processWindow(1.0, AudioLabel.silence, 1.0)
        val ev2 = debouncer.processWindow(1.5, AudioLabel.silence, 1.0)
        val ev3 = debouncer.processWindow(2.0, AudioLabel.bark, 0.9)
        val ev4 = debouncer.processWindow(2.5, AudioLabel.bark, 0.95)

        assertThat(ev1).isNotNull()
        assertThat(ev2).isNotNull()
        assertThat(ev3).isNotNull()
        assertThat(ev4).isNotNull()
    }

    @Test
    fun `checkSilence correctly detects silence and non-silence`() {
        val debouncer = AudioEventDebouncer(silenceRms = 0.005)

        val silent = FloatArray(15600) { 0.0f }
        val res1 = debouncer.checkSilence(silent)
        assertThat(res1).isNotNull()
        assertThat(res1!!.first).isEqualTo(AudioLabel.silence)
        assertThat(res1.second).isEqualTo(1.0)

        // Non-silent tone (sine wave amplitude 0.1, RMS ~ 0.0707)
        val loud = FloatArray(15600) { i -> (0.1 * Math.sin(2.0 * Math.PI * 440.0 * i / 16000.0)).toFloat() }
        val res2 = debouncer.checkSilence(loud)
        assertThat(res2).isNull()
    }

    @Test
    fun `AudioChunkBuffer correctly windows 5s PCM fixture`() {
        val pcmStream = javaClass.getResourceAsStream("/audio/audio_5s_16k.pcm")
        assertThat(pcmStream).isNotNull()
        val bytes = pcmStream!!.readBytes()
        assertThat(bytes.size).isEqualTo(160000)

        val shorts = ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN).asShortBuffer()
        val floatSamples = FloatArray(shorts.remaining())
        for (i in floatSamples.indices) {
            floatSamples[i] = shorts.get(i) / 32767.0f
        }

        val chunkBuffer = AudioChunkBuffer(
            sampleRate = 16000,
            windowSize = 15600,
            hopSize = 7680,
            maxGapS = 0.5,
        )

        // Push in arbitrary chunk sizes (e.g. 1024 samples)
        val windows = mutableListOf<Pair<Double, FloatArray>>()
        var offset = 0
        var chunkTs = 0.0
        while (offset < floatSamples.size) {
            val sz = minOf(1024, floatSamples.size - offset)
            val chunk = FloatArray(sz)
            System.arraycopy(floatSamples, offset, chunk, 0, sz)
            windows.addAll(chunkBuffer.push(chunkTs, chunk))
            offset += sz
            chunkTs += sz.toDouble() / 16000.0
        }

        // Exactly 9 windows produced
        assertThat(windows.size).isEqualTo(9)
        val expectedTimestamps = listOf(0.975, 1.455, 1.935, 2.415, 2.895, 3.375, 3.855, 4.335, 4.815)
        for (i in expectedTimestamps.indices) {
            assertThat(windows[i].first).isWithin(1e-5).of(expectedTimestamps[i])
            assertThat(windows[i].second.size).isEqualTo(15600)
        }
    }

    @Test
    fun `AudioChunkBuffer resets on gap exceeding maxGapS`() {
        val chunkBuffer = AudioChunkBuffer(maxGapS = 0.5)

        // Push 1000 samples at ts=0.0
        val chunk1 = FloatArray(1000) { 0.1f }
        val w1 = chunkBuffer.push(0.0, chunk1)
        assertThat(w1).isEmpty()

        // Push next chunk with gap of 2.0s -> buffer should be cleared before adding chunk2
        val chunk2 = FloatArray(1000) { 0.2f }
        val w2 = chunkBuffer.push(2.0, chunk2)
        assertThat(w2).isEmpty()
    }
}
