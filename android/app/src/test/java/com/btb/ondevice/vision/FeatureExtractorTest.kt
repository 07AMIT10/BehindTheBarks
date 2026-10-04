package com.btb.ondevice.vision

import com.btb.ondevice.config.DataConfig
import com.btb.ondevice.contracts.ContractsJson
import com.btb.ondevice.contracts.Features
import com.google.common.truth.Truth.assertThat
import com.google.common.truth.Truth.assertWithMessage
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.decodeFromJsonElement
import kotlinx.serialization.json.double
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.jupiter.api.Test
import org.junit.jupiter.params.ParameterizedTest
import org.junit.jupiter.params.provider.ValueSource

class FeatureExtractorTest {
    @ParameterizedTest(name = "features replay matches golden fixture on {0}")
    @ValueSource(strings = [
        "eating_home",
        "idle_labrador",
        "relaxed_chihuahua",
        "treat_beach",
        "treat_poodle",
        "vocal_chained",
        "waiting_corgi",
    ])
    fun `replaying logic fixture produces golden feature parity`(clipName: String) {
        val stream = javaClass.getResourceAsStream("/logic/$clipName.jsonl")
        assertThat(stream).isNotNull()

        val configStream = javaClass.getResourceAsStream("/config_data.json")
        assertThat(configStream).isNotNull()
        val config = DataConfig.fromJson(configStream!!.bufferedReader().use { it.readText() })

        val fx = FeatureExtractor(config)

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

                val kps = obj["keypoints"]?.let { el ->
                    if (el.toString() == "null") null else el.jsonObject.mapValues { (_, v) ->
                        if (v.toString() == "null") null else v.jsonArray.map { it.jsonPrimitive.double }
                    }
                }

                val face = obj["face"]?.let { el ->
                    if (el.toString() == "null") null else el.jsonArray.map { pt ->
                        pt.jsonArray.map { it.jsonPrimitive.double }
                    }
                }

                val wagRoi = obj["wag_roi"]?.let { el ->
                    if (el.toString() == "null") null else el.jsonPrimitive.doubleOrNull
                }

                val expectedFeatures = ContractsJson.decodeFromJsonElement<Features>(obj["features"]!!)

                val event = fx.update(
                    ts = ts,
                    bbox = bbox,
                    bboxConf = bboxConf,
                    inFeedingZone = expectedFeatures.inFeedingZone ?: false,
                    kps = kps,
                    lms = face,
                    roiWagHz = wagRoi,
                )

                val actual = event.features
                assertWithMessage("frame $idx in $clipName inFeedingZone").that(actual.inFeedingZone)
                    .isEqualTo(expectedFeatures.inFeedingZone)

                assertWithMessage("frame $idx in $clipName earPosition").that(actual.earPosition)
                    .isEqualTo(expectedFeatures.earPosition)

                assertDoubleMatch("frame $idx in $clipName tailHeight", actual.tailHeight, expectedFeatures.tailHeight)
                assertDoubleMatch("frame $idx in $clipName tailWagHz", actual.tailWagHz, expectedFeatures.tailWagHz)
                assertDoubleMatch("frame $idx in $clipName mouthOpen", actual.mouthOpen, expectedFeatures.mouthOpen)
                assertDoubleMatch("frame $idx in $clipName bodyLowering", actual.bodyLowering, expectedFeatures.bodyLowering)
                assertDoubleMatch("frame $idx in $clipName motionEnergy", actual.motionEnergy, expectedFeatures.motionEnergy)
            }
        }
    }

    private fun assertDoubleMatch(msg: String, actual: Double?, expected: Double?) {
        if (expected == null) {
            assertWithMessage("$msg expected null").that(actual).isNull()
        } else {
            assertWithMessage("$msg expected non-null").that(actual).isNotNull()
            assertWithMessage(msg).that(actual!!).isWithin(0.07).of(expected)
        }
    }
}
