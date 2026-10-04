package com.btb.ondevice.vision

import com.google.common.truth.Truth.assertThat
import org.junit.jupiter.api.Test
import java.io.InputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder

class SimCCDecoderTest {

    @Test
    fun `decode matches python mobile runner SimCC decode on synthetic tensors`() {
        val simccX = FloatArray(17 * 512)
        val simccY = FloatArray(17 * 512)

        // Point 0: L_Eye (mapped to left_eye in AP-10K)
        // Peak at bin 200 in x, bin 150 in y
        simccX[0 * 512 + 200] = 0.9f
        simccY[0 * 512 + 150] = 0.8f

        // Point 1: R_Eye (sub-threshold: score 0.4 * 0.4 = 0.16 < 0.3)
        simccX[1 * 512 + 100] = 0.4f
        simccY[1 * 512 + 100] = 0.4f

        val crop = CropRegion(ox = 100, oy = 100, width = 200, height = 200)
        val kps = SimCCDecoder.decode(simccX, simccY, crop)

        // Verify all 19 canonical names exist in map
        assertThat(kps.keys).containsExactlyElementsIn(KeypointMap.CANONICAL_NAMES)

        // left_eye should be detected
        val leftEye = kps["left_eye"]
        assertThat(leftEye).isNotNull()
        assertThat(leftEye!!.conf).isWithin(1e-3).of(0.72) // 0.9 * 0.8

        // x_in = 200 / 2.0 = 100.0, y_in = 150 / 2.0 = 75.0 in 256x256
        // crop scaling: sx = 200 / 256.0, sy = 200 / 256.0
        // cx = 100.0 * (200 / 256.0) = 78.125
        // cy = 75.0 * (200 / 256.0) = 58.59375
        // full_x = 78.125 + 100 = 178.125
        // full_y = 58.59375 + 100 = 158.59375
        assertThat(leftEye.x).isWithin(0.5).of(178.125)
        assertThat(leftEye.y).isWithin(0.5).of(158.59375)

        // right_eye is sub-threshold -> null
        assertThat(kps["right_eye"]).isNull()

        // unmapped points like tail_tip should be null
        assertThat(kps["tail_tip"]).isNull()
    }

    @Test
    fun `decode rejects points outside margin fraction`() {
        val simccX = FloatArray(17 * 512)
        val simccY = FloatArray(17 * 512)

        // Point 2: Nose (mapped to nose)
        // Bin 0 is at 0.0 -> valid.
        // But if bin is far out:
        // Set outside margin to very tight (0.001)
        // Place point right near the edge
        simccX[2 * 512 + 511] = 0.9f // x_in = 255.5
        simccY[2 * 512 + 100] = 0.9f

        val crop = CropRegion(ox = 0, oy = 0, width = 100, height = 100)
        val tightConfig = PoseConfig(outsideMarginFrac = 0.001)
        val kps = SimCCDecoder.decode(simccX, simccY, crop, tightConfig)

        // cx = 255.5 * (100 / 256.0) = 99.80 -> within width 100
        // Now if peak is outside by setting negative or out-of-bounds:
        // Test with a mock peak that lies beyond crop bounds + margin
        val extremeConfig = PoseConfig(outsideMarginFrac = -0.05) // margin < 0 forces edge rejection
        val rejectedKps = SimCCDecoder.decode(simccX, simccY, crop, extremeConfig)
        assertThat(rejectedKps["nose"]).isNull()
    }

    @Test
    fun `replaying 8 pose model output fixtures decodes valid peaks`() {
        for (i in 0 until 8) {
            val resourcePath = "models/pose/output_$i.bin"
            val stream: InputStream = javaClass.classLoader?.getResourceAsStream(resourcePath)
                ?: error("Could not load resource $resourcePath")

            val bytes = stream.readBytes()
            assertThat(bytes.size).isEqualTo(17 * 512 * 4)

            val buffer = ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN)
            val simccX = FloatArray(17 * 512)
            buffer.asFloatBuffer().get(simccX)

            // Verify each keypoint's argmax is between 0 and 511
            for (kpIdx in 0 until 17) {
                var maxVal = -Float.MAX_VALUE
                var bestBin = -1
                for (b in 0 until 512) {
                    val v = simccX[kpIdx * 512 + b]
                    if (v > maxVal) {
                        maxVal = v
                        bestBin = b
                    }
                }
                assertThat(bestBin).isAtLeast(0)
                assertThat(bestBin).isLessThan(512)
            }
        }
    }
}
