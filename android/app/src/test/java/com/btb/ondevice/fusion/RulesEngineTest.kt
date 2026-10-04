package com.btb.ondevice.fusion

import com.btb.ondevice.config.DataConfig
import com.btb.ondevice.contracts.AudioEvent
import com.btb.ondevice.contracts.ContractsJson
import com.btb.ondevice.contracts.EMOTIONS
import com.btb.ondevice.contracts.Features
import com.btb.ondevice.contracts.FrameEvent
import com.btb.ondevice.contracts.RulesLabel
import com.google.common.truth.Truth.assertThat
import com.google.common.truth.Truth.assertWithMessage
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.decodeFromJsonElement
import kotlinx.serialization.json.double
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.jupiter.api.Test
import org.junit.jupiter.params.ParameterizedTest
import org.junit.jupiter.params.provider.ValueSource

class RulesEngineTest {
    @ParameterizedTest(name = "rules replay matches golden fixture on {0}")
    @ValueSource(strings = [
        "eating_home",
        "idle_labrador",
        "relaxed_chihuahua",
        "treat_beach",
        "treat_poodle",
        "vocal_chained",
        "waiting_corgi",
    ])
    fun `replaying logic fixture produces golden rules parity`(clipName: String) {
        val stream = javaClass.getResourceAsStream("/logic/$clipName.jsonl")
        assertThat(stream).isNotNull()

        val configStream = javaClass.getResourceAsStream("/config_data.json")
        assertThat(configStream).isNotNull()
        val config = DataConfig.fromJson(configStream!!.bufferedReader().use { it.readText() })

        val engine = RulesEngine(config)

        stream!!.bufferedReader().useLines { lines ->
            lines.forEachIndexed { idx, line ->
                if (line.isBlank()) return@forEachIndexed
                val obj = Json.parseToJsonElement(line).jsonObject

                val ts = obj["ts"]!!.jsonPrimitive.double
                val detArr = obj["det"]?.let { el ->
                    if (el.toString() == "null") null else el.jsonArray.map { it.jsonPrimitive.double }
                }
                val bbox = if (detArr != null && detArr.size >= 4) detArr.take(4) else null
                val bboxConf = if (detArr != null && detArr.size >= 5) detArr[4] else null

                val features = ContractsJson.decodeFromJsonElement<Features>(obj["features"]!!)
                val expectedRules = ContractsJson.decodeFromJsonElement<RulesLabel>(obj["rules"]!!)

                val audioEvents = obj["audio"]?.jsonArray?.map {
                    ContractsJson.decodeFromJsonElement<AudioEvent>(it)
                } ?: emptyList()

                val treat = obj["treat"]?.jsonPrimitive?.content?.toBoolean() ?: false

                val frame = FrameEvent(
                    ts = ts,
                    source = "live",
                    dogDetected = detArr != null,
                    bbox = bbox,
                    bboxConf = bboxConf,
                    features = features,
                )

                val actual = engine.update(
                    frame = frame,
                    audioEvents = audioEvents,
                    treatEventRecent = treat,
                )

                assertWithMessage("frame $idx in $clipName emotion label").that(actual.emotion)
                    .isEqualTo(expectedRules.emotion)

                assertWithMessage("frame $idx in $clipName confidence").that(actual.confidence)
                    .isWithin(1e-4).of(expectedRules.confidence)

                for (e in EMOTIONS) {
                    val actScore = actual.scores[e] ?: 0.0
                    val expScore = expectedRules.scores[e] ?: 0.0
                    assertWithMessage("frame $idx in $clipName score for $e").that(actScore)
                        .isWithin(1e-4).of(expScore)
                }
            }
        }
    }
}
