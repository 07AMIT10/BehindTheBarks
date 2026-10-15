package com.btb.ondevice.audio

import com.google.common.truth.Truth.assertThat
import com.google.common.truth.Truth.assertWithMessage
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertThrows

/**
 * The phone's IMA-ADPCM encoder against the shared vectors (tests/vectors/audio_v1.json, copied to
 * src/test/resources/audio/ and kept identical by tests/web/test_audio_codec.py). The Python and Kotlin
 * encoders must produce identical bytes; the decoder must reproduce the reference samples exactly.
 */
class AdpcmCodecTest {

    private val doc: JsonObject by lazy {
        val stream = javaClass.classLoader?.getResourceAsStream("audio/audio_v1.json")
        assertThat(stream).isNotNull()
        Json.parseToJsonElement(stream!!.bufferedReader().use { it.readText() }).jsonObject
    }

    private fun vectors() = doc["vectors"]!!.jsonArray.map { it.jsonObject }

    private fun vector(name: String): JsonObject =
        vectors().first { it["name"]!!.jsonPrimitive.content == name }

    private fun ints(array: kotlinx.serialization.json.JsonArray) =
        ShortArray(array.size) { array[it].jsonPrimitive.int.toShort() }

    private fun hex(s: String): ByteArray = ByteArray(s.length / 2) { s.substring(2 * it, 2 * it + 2).toInt(16).toByte() }

    private fun toHex(bytes: ByteArray): String = bytes.joinToString("") { "%02x".format(it.toInt() and 0xFF) }

    @Test
    fun `tables match the shared vectors file`() {
        val steps = doc["step_table"]!!.jsonArray.map { it.jsonPrimitive.int }
        assertThat(steps).hasSize(89)
        assertThat(steps.first()).isEqualTo(7)
        assertThat(steps.last()).isEqualTo(32767)
        assertThat(doc["index_table"]!!.jsonArray.map { it.jsonPrimitive.int })
            .containsExactly(-1, -1, -1, -1, 2, 4, 6, 8, -1, -1, -1, -1, 2, 4, 6, 8).inOrder()
    }

    @Test
    fun `encoder produces byte-identical blocks for every vector`() {
        for (v in vectors()) {
            val name = v["name"]!!.jsonPrimitive.content
            val block = AdpcmCodec.encodeBlock(ints(v["samples"]!!.jsonArray))
            assertWithMessage(name).that(toHex(block)).isEqualTo(v["block_hex"]!!.jsonPrimitive.content)
        }
    }

    @Test
    fun `decoder reproduces the reference samples exactly`() {
        for (v in vectors()) {
            val name = v["name"]!!.jsonPrimitive.content
            val decoded = AdpcmCodec.decodeBlock(hex(v["block_hex"]!!.jsonPrimitive.content))
            assertWithMessage(name).that(decoded.toList()).isEqualTo(ints(v["decoded"]!!.jsonArray).toList())
        }
    }

    @Test
    fun `reconstruction error stays within the recorded bound`() {
        for (v in vectors()) {
            val name = v["name"]!!.jsonPrimitive.content
            val input = ints(v["samples"]!!.jsonArray)
            val decoded = AdpcmCodec.decodeBlock(AdpcmCodec.encodeBlock(input))
            var err = 0
            for (i in input.indices) err = maxOf(err, kotlin.math.abs(input[i] - decoded[i]))
            assertWithMessage(name).that(err).isEqualTo(v["max_abs_error"]!!.jsonPrimitive.int)
            assertWithMessage(name).that(err).isAtMost(v["max_abs_error_bound"]!!.jsonPrimitive.int)
        }
    }

    @Test
    fun `frame packing matches the vector frames`() {
        for (v in vectors()) {
            val frame = v["frame"]!!.jsonObject
            val packed = AdpcmCodec.packFrame(
                AdpcmCodec.CODEC_IMA_ADPCM,
                frame["seq"]!!.jsonPrimitive.int.toLong(),
                frame["t_ms"]!!.jsonPrimitive.int.toLong(),
                hex(v["block_hex"]!!.jsonPrimitive.content),
            )
            assertThat(toHex(packed)).isEqualTo(v["frame_hex"]!!.jsonPrimitive.content)
        }
    }

    @Test
    fun `encoder refuses chunks that are not exactly one 100 ms chunk`() {
        for (c in doc["reject_encode"]!!.jsonArray.map { it.jsonObject }) {
            val samples = ShortArray(c["samples"]!!.jsonArray.size)
            assertThrows<IllegalArgumentException> { AdpcmCodec.encodeBlock(samples) }
        }
    }

    @Test
    fun `decoder refuses every malformed block in the vectors file`() {
        for (c in doc["reject_decode"]!!.jsonArray.map { it.jsonObject }) {
            assertThrows<IllegalArgumentException>(c["name"]!!.jsonPrimitive.content) {
                AdpcmCodec.decodeBlock(hex(c["block_hex"]!!.jsonPrimitive.content))
            }
        }
    }

    @Test
    fun `frame packing refuses reserved codecs and wrong payload lengths`() {
        val block = ByteArray(AdpcmCodec.BLOCK_BYTES)
        assertThrows<IllegalArgumentException> { AdpcmCodec.packFrame(2, 0, 0, block) }
        assertThrows<IllegalArgumentException> { AdpcmCodec.packFrame(1, 0, 0, ByteArray(803)) }
        assertThrows<IllegalArgumentException> { AdpcmCodec.packFrame(0, 0, 0, ByteArray(10)) }
        assertThrows<IllegalArgumentException> { AdpcmCodec.packFrame(1, 1L shl 32, 0, block) }
    }
}
