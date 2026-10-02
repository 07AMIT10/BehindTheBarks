package com.btb.ondevice.contracts

import com.google.common.truth.Truth.assertThat
import kotlinx.serialization.ExperimentalSerializationApi
import kotlinx.serialization.MissingFieldException
import kotlinx.serialization.SerializationException
import kotlinx.serialization.decodeFromString
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.jupiter.api.Test
import org.junit.jupiter.params.ParameterizedTest
import org.junit.jupiter.params.provider.MethodSource
import org.junit.jupiter.params.provider.ValueSource

/**
 * The fixtures are the pydantic models' own `to_json()` output, committed by
 * `scripts/gen_kotlin_types.py` and regenerated whenever backend/contracts.py changes, so these
 * tests fail if the Kotlin mirror and the wire format drift apart.
 */
class ContractsTest {
    @ParameterizedTest(name = "{0} round-trips with identical key sets and nulls kept")
    @MethodSource("modelFixtures")
    fun `a contract round-trips through Kotlin unchanged`(fixture: String) {
        val fromPython = fixtureJson(fixture)
        val reEncoded = reEncode(fixture, fromPython)

        assertJsonEquals(Json.parseToJsonElement(fromPython), Json.parseToJsonElement(reEncoded))
    }

    @Test
    fun `explicit nulls are encoded, not dropped`() {
        val encoded = reEncode("emotion_state.json", fixtureJson("emotion_state.json"))

        assertThat(Json.parseToJsonElement(encoded).jsonObject).containsKey("snapshot")
        assertThat(Json.parseToJsonElement(encoded).jsonObject["snapshot"]?.jsonPrimitive?.content)
            .isEqualTo("null")
        // Default-valued fields (FrameEvent.features, FrameEvent.body_keypoints) are on the wire too.
        val frame = Json.parseToJsonElement(
            reEncode("frame_event.json", fixtureJson("frame_event.json")),
        ).jsonObject
        assertThat(frame["body_keypoints"]!!.jsonObject).containsKey("tail_tip")
        assertThat(frame["features"]!!.jsonObject).containsKey("in_feeding_zone")
    }

    @ParameterizedTest(name = "{0}.json rejects an unknown key, like extra=forbid")
    @ValueSource(strings = ["features", "frame_event", "audio_event", "rules_label", "emotion_state",
        "llm_result"])
    fun `an unknown key is a decode failure`(model: String) {
        val withUnknownKey = fixtureJson("$model.json").dropLast(1) + ", \"surprise\": 1}"

        val thrown = runCatching { decode(model, withUnknownKey) }.exceptionOrNull()

        assertThat(thrown).isInstanceOf(SerializationException::class.java)
    }

    @OptIn(ExperimentalSerializationApi::class)
    @Test
    fun `a missing required field is a decode failure`() {
        val withoutTs = fixtureJson("audio_event.json").replaceFirst("\"ts\":1727340000.5,", "")

        val thrown = runCatching { decode("audio_event", withoutTs) }.exceptionOrNull()

        assertThat(thrown).isInstanceOf(MissingFieldException::class.java)
    }

    @Test
    fun `a label outside the vocabulary is a decode failure`() {
        val bogus = fixtureJson("audio_event.json").replace("\"bark\"", "\"moo\"")

        assertThat(runCatching { decode("audio_event", bogus) }.exceptionOrNull())
            .isInstanceOf(SerializationException::class.java)
        assertThat(runCatching { ContractsJson.decodeFromString<Emotion>("\"elated\"") }.exceptionOrNull())
            .isInstanceOf(SerializationException::class.java)
    }

    @Test
    fun `the emotion enum serialises by its lower-case name`() {
        val encoded = EMOTIONS.joinToString(",") { ContractsJson.encodeToString(it) }

        assertThat(encoded)
            .isEqualTo("\"happy\",\"excited\",\"relaxed\",\"anxious\",\"fearful\"," +
                "\"aggressive\",\"disinterested\",\"unknown\"")
        EMOTIONS.forEach { emotion ->
            // The entry name is the wire value: no @SerialName translation in between.
            assertThat(emotion.name).isEqualTo(emotion.name.lowercase())
            assertThat(ContractsJson.decodeFromString<Emotion>("\"$emotion\"")).isEqualTo(emotion)
        }
    }

    @Test
    fun `rules label scores carry all eight emotions`() {
        val label = ContractsJson.decodeFromString<RulesLabel>(fixtureJson("rules_label.json"))

        assertThat(label.scores.keys).containsExactlyElementsIn(EMOTIONS)
        assertThat(label.scores.keys).hasSize(8)
        val scores = Json.parseToJsonElement(ContractsJson.encodeToString(label))
            .jsonObject["scores"]!!.jsonObject
        EMOTIONS.forEach { assertThat(scores).containsKey(it.name) }
        assertThat(scores).hasSize(8)
    }

    @Test
    fun `a null keypoint stays null instead of being guessed`() {
        val frame = ContractsJson.decodeFromString<FrameEvent>(fixtureJson("frame_event.json"))

        assertThat(frame.bodyKeypoints["tail_base"]).isEqualTo(listOf(300.0, 400.0, 0.91))
        assertThat(frame.bodyKeypoints["tail_tip"]).isNull()
        assertThat(frame.faceLandmarks).isNull()
        assertThat(frame.bbox).isEqualTo(listOf(120.5, 240.0, 610.0, 480.25))
    }

    companion object {
        @JvmStatic
        fun modelFixtures(): List<String> = listOf(
            "features.json",
            "frame_event.json",
            "audio_event.json",
            "rules_label.json",
            "emotion_state.json",
            "llm_result.json",
        )

        /** The fixture as the pydantic model itself wrote it. */
        fun fixtureJson(name: String): String =
            checkNotNull(ContractsTest::class.java.getResourceAsStream("/contracts/$name")) {
                "missing committed fixture contracts/$name; run: python scripts/gen_kotlin_types.py"
            }.bufferedReader().use { it.readText() }

        fun decode(model: String, json: String): Any = when (model.removeSuffix(".json")) {
            "features" -> ContractsJson.decodeFromString<Features>(json)
            "frame_event" -> ContractsJson.decodeFromString<FrameEvent>(json)
            "audio_event" -> ContractsJson.decodeFromString<AudioEvent>(json)
            "rules_label" -> ContractsJson.decodeFromString<RulesLabel>(json)
            "emotion_state" -> ContractsJson.decodeFromString<EmotionState>(json)
            "llm_result" -> ContractsJson.decodeFromString<LLMResult>(json)
            else -> error("no contract named $model")
        }

        fun reEncode(fixture: String, json: String): String = when (fixture) {
            "features.json" -> ContractsJson.encodeToString(ContractsJson.decodeFromString<Features>(json))
            "frame_event.json" -> ContractsJson.encodeToString(ContractsJson.decodeFromString<FrameEvent>(json))
            "audio_event.json" -> ContractsJson.encodeToString(ContractsJson.decodeFromString<AudioEvent>(json))
            "rules_label.json" -> ContractsJson.encodeToString(ContractsJson.decodeFromString<RulesLabel>(json))
            "emotion_state.json" -> ContractsJson.encodeToString(ContractsJson.decodeFromString<EmotionState>(json))
            "llm_result.json" -> ContractsJson.encodeToString(ContractsJson.decodeFromString<LLMResult>(json))
            else -> error("no contract fixture named $fixture")
        }

        /** Same keys, same nulls, same values; numbers compared within a tolerance. */
        fun assertJsonEquals(expected: JsonElement, actual: JsonElement, path: String = "value") {
            when {
                expected is JsonObject && actual is JsonObject -> {
                    assertThat(actual.keys).containsExactlyElementsIn(expected.keys)
                    expected.keys.forEach { key ->
                        assertJsonEquals(expected.getValue(key), actual.getValue(key), "$path.$key")
                    }
                }
                expected is JsonArray && actual is JsonArray -> {
                    assertThat(actual.size).isEqualTo(expected.size)
                    expected.forEachIndexed { index, element ->
                        assertJsonEquals(element, actual[index], "$path[$index]")
                    }
                }
                else -> {
                    val want = expected.jsonPrimitive
                    val got = actual.jsonPrimitive
                    if (want.doubleOrNull != null && got.doubleOrNull != null) {
                        assertThat(got.doubleOrNull!!).isWithin(1e-9).of(want.doubleOrNull!!)
                    } else {
                        assertThat(got.content).isEqualTo(want.content)
                    }
                }
            }
        }
    }
}