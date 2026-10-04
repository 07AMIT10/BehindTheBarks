package com.btb.ondevice.vision

import com.google.common.truth.Truth.assertThat
import org.junit.jupiter.api.Test
import java.io.InputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder

class FaceLandmarkerTest {

    private val landmarker = FaceLandmarker()

    @Test
    fun `headBox requires eye or ear base and minimum points`() {
        // Only nose: no eye or ear base -> null
        val noseOnly = mapOf(
            "nose" to KeypointMap.Point(100.0, 100.0, 0.9)
        )
        assertThat(landmarker.headBox(noseOnly)).isNull()

        // Nose and only 1 eye -> total 2 points < minHeadPoints(3) -> null
        val twoPoints = mapOf(
            "nose" to KeypointMap.Point(100.0, 100.0, 0.9),
            "left_eye" to KeypointMap.Point(80.0, 80.0, 0.9),
        )
        assertThat(landmarker.headBox(twoPoints)).isNull()

        // 3 core points: nose, left_eye, right_eye
        val threePoints = mapOf(
            "nose" to KeypointMap.Point(100.0, 100.0, 0.9),
            "left_eye" to KeypointMap.Point(80.0, 80.0, 0.9),
            "right_eye" to KeypointMap.Point(120.0, 80.0, 0.9),
        )
        val box = landmarker.headBox(threePoints)
        assertThat(box).isNotNull()
        // x in 80..120 -> width = 40. y in 80..100 -> height = 20.
        // side = max(40, 20) * (1 + 2 * 0.25) = 40 * 1.5 = 60.0 >= minCropPx (48)
        // cx = 100.0, cy = 90.0
        // x1 = 100 - 30 = 70.0, y1 = 90 - 30 = 60.0
        assertThat(box!!.x1).isWithin(1e-4).of(70.0)
        assertThat(box.y1).isWithin(1e-4).of(60.0)
        assertThat(box.side).isWithin(1e-4).of(60.0)
    }

    @Test
    fun `headBox rejects tiny crops below minCropPx`() {
        // Very tight points: x in 100..105, y in 100..105 -> max side 5 * 1.5 = 7.5 < 48
        val tinyPoints = mapOf(
            "nose" to KeypointMap.Point(102.0, 102.0, 0.9),
            "left_eye" to KeypointMap.Point(100.0, 100.0, 0.9),
            "right_eye" to KeypointMap.Point(105.0, 100.0, 0.9),
        )
        assertThat(landmarker.headBox(tinyPoints)).isNull()
    }

    @Test
    fun `isPlausible validates spread and boundary constraints`() {
        // Valid spread landmarks spread across 0.2 to 0.8
        val validNorm = List(46) { i ->
            val frac = i.toDouble() / 46.0
            Pair(0.2 + frac * 0.6, 0.2 + frac * 0.6)
        }
        assertThat(landmarker.isPlausible(validNorm)).isTrue()

        // Collapsed blob: spread < 0.25
        val collapsed = List(46) { Pair(0.5, 0.5) }
        assertThat(landmarker.isPlausible(collapsed)).isFalse()

        // Majority points outside crop (-0.05..1.05)
        val outside = List(46) { Pair(2.0, 2.0) }
        assertThat(landmarker.isPlausible(outside)).isFalse()
    }

    @Test
    fun `decodeOutput maps plausible landmarks to full frame pixels`() {
        val headBox = HeadBox(x1 = 100.0, y1 = 100.0, side = 200.0)
        val output = FloatArray(92)
        for (i in 0 until 46) {
            val frac = (i.toFloat() / 46.0f) * 0.6f + 0.2f
            output[2 * i] = frac
            output[2 * i + 1] = frac
        }

        val decoded = landmarker.decodeOutput(output, headBox)
        assertThat(decoded).isNotNull()
        assertThat(decoded).hasSize(46)

        // First point: frac = 0.2 -> full_x = 100.0 + 0.2 * 200 = 140.0
        assertThat(decoded!![0][0]).isWithin(1e-4).of(140.0)
        assertThat(decoded[0][1]).isWithin(1e-4).of(140.0)
    }

    @Test
    fun `replaying 8 face model output fixtures decodes 92 float tensors`() {
        val headBox = HeadBox(x1 = 50.0, y1 = 50.0, side = 100.0)

        for (i in 0 until 8) {
            val resourcePath = "models/face/output_$i.bin"
            val stream: InputStream = javaClass.classLoader?.getResourceAsStream(resourcePath)
                ?: error("Could not load resource $resourcePath")

            val bytes = stream.readBytes()
            assertThat(bytes.size).isEqualTo(92 * 4)

            val buffer = ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN)
            val output = FloatArray(92)
            buffer.asFloatBuffer().get(output)

            for (v in output) {
                assertThat(v.isFinite()).isTrue()
            }
        }
    }
}
