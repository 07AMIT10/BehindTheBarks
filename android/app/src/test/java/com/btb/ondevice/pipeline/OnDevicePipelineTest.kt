package com.btb.ondevice.pipeline

import com.btb.ondevice.config.DataConfig
import com.btb.ondevice.contracts.AudioEvent
import com.btb.ondevice.contracts.ContractsJson
import com.btb.ondevice.contracts.Features
import com.btb.ondevice.contracts.FrameEvent
import com.btb.ondevice.contracts.RulesLabel
import com.google.common.truth.Truth.assertThat
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.decodeFromJsonElement
import kotlinx.serialization.json.double
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.jupiter.api.Test
import org.junit.jupiter.params.ParameterizedTest
import org.junit.jupiter.params.provider.ValueSource
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

class OnDevicePipelineTest {

    @ParameterizedTest(name = "pipeline replay matches golden fixture on {0}")
    @ValueSource(strings = [
        "eating_home",
        "waiting_corgi",
        "relaxed_chihuahua",
    ])
    fun `processReplayFrame produces golden parity on logic fixtures`(clipName: String) {
        val stream = javaClass.getResourceAsStream("/logic/$clipName.jsonl")
            ?: error("Could not load /logic/$clipName.jsonl")
        val configStream = javaClass.getResourceAsStream("/config_data.json")
            ?: error("Could not load /config_data.json")
        val config = DataConfig.fromJson(configStream.bufferedReader().readText())

        val pipeline = OnDevicePipeline(config)

        stream.bufferedReader().useLines { lines ->
            lines.forEachIndexed { idx, line ->
                if (line.isBlank()) return@forEachIndexed
                val obj = Json.parseToJsonElement(line).jsonObject

                val ts = obj["ts"]!!.jsonPrimitive.double
                val detArr = obj["det"]?.let { el ->
                    if (el.toString() == "null") null else el.jsonArray.map { it.jsonPrimitive.double }
                }
                val bbox = if (detArr != null && detArr.size >= 4) detArr.take(4) else null
                val bboxConf = if (detArr != null && detArr.size >= 5) detArr[4] else null

                val kps = obj["keypoints"]?.let { el ->
                    if (el.toString() == "null") null else el.jsonObject.mapValues { (_, v) ->
                        if (v.toString() == "null") null else v.jsonArray.map { it.jsonPrimitive.double }
                    }
                } ?: emptyMap()

                val lms = obj["face"]?.let { el ->
                    if (el.toString() == "null") null
                    else el.jsonArray.map { pt -> pt.jsonArray.map { it.jsonPrimitive.double } }
                }

                val wagHz = obj["wag_roi"]?.let {
                    if (it.toString() == "null") null else it.jsonPrimitive.double
                }

                val audioEvents = obj["audio"]?.jsonArray?.map {
                    ContractsJson.decodeFromJsonElement<AudioEvent>(it)
                } ?: emptyList()

                val treat = obj["treat"]?.jsonPrimitive?.content?.toBoolean() ?: false

                val expectedFeatures = ContractsJson.decodeFromJsonElement<Features>(obj["features"]!!)
                val expectedRules = ContractsJson.decodeFromJsonElement<RulesLabel>(obj["rules"]!!)

                val (frameEvent, rulesLabel) = pipeline.processReplayFrame(
                    ts = ts,
                    bbox = bbox,
                    bboxConf = bboxConf,
                    bodyKeypoints = kps,
                    faceLandmarks = lms,
                    audioEvents = audioEvents,
                    isTreatRecent = treat,
                    wagHz = wagHz,
                    inFeedingZone = expectedFeatures.inFeedingZone ?: false,
                    precomputedFeatures = expectedFeatures,
                )

                assertThat(frameEvent.ts).isEqualTo(ts)
                assertThat(frameEvent.dogDetected).isEqualTo(bbox != null)

                if (rulesLabel != null) {
                    assertThat(rulesLabel.emotion).isEqualTo(expectedRules.emotion)
                    assertThat(rulesLabel.confidence).isWithin(1e-4).of(expectedRules.confidence)
                }
            }
        }
    }

    @Test
    fun `inferenceExecutor drops non-blocking tasks when busy`() {
        val executor = InferenceExecutor()
        val blockLatch = CountDownLatch(1)
        val startedLatch = CountDownLatch(1)

        // First task holds the thread
        val firstSubmitted = executor.submitLatest {
            startedLatch.countDown()
            blockLatch.await(500, TimeUnit.MILLISECONDS)
        }
        assertThat(firstSubmitted).isTrue()

        // Wait until first task is executing
        startedLatch.await(100, TimeUnit.MILLISECONDS)

        // Second task should be dropped under latest-wins policy
        val secondSubmitted = executor.submitLatest {
            // Should not run
        }
        assertThat(secondSubmitted).isFalse()

        // Release latch
        blockLatch.countDown()
        executor.close()
    }
}
