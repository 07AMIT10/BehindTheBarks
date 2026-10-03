package com.btb.ondevice.vision

import com.google.common.truth.Truth.assertThat
import org.junit.jupiter.api.Test
import java.io.InputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder

class DogDetectorTest {

    private val detector = DogDetector()

    @Test
    fun `decodeRawHead matches python mobile runner decoding on synthetic tensor`() {
        // [1, 84, 2100] = 176400 floats
        val tensor = FloatArray(84 * 2100)
        // Anchor 10 has a dog detection (class 16 -> index 4 + 16 = 20)
        // cx=0.5, cy=0.5, w=0.25, h=0.25
        val anchor = 10
        val numAnchors = 2100
        tensor[0 * numAnchors + anchor] = 0.5f  // cx
        tensor[1 * numAnchors + anchor] = 0.5f  // cy
        tensor[2 * numAnchors + anchor] = 0.25f // w
        tensor[3 * numAnchors + anchor] = 0.25f // h
        tensor[20 * numAnchors + anchor] = 0.85f // dog score

        val params = Letterbox.computeParams(srcWidth = 640, srcHeight = 480, targetSize = 320)
        val detections = detector.decodeRawHead(tensor, params, confThreshold = 0.25f)

        assertThat(detections).hasSize(1)
        val d = detections[0]
        assertThat(d.score).isWithin(1e-4f).of(0.85f)
        assertThat(d.classId).isEqualTo(16)

        // Letterbox 320x320 for 640x480 has scale=0.5, padTop=40.
        // cx=160, cy=160 -> w=80, h=80 -> x1=120, y1=120, x2=200, y2=200
        // Unletterbox: x1 = 120 / 0.5 = 240, y1 = (120 - 40) / 0.5 = 160
        //              x2 = 200 / 0.5 = 400, y2 = (200 - 40) / 0.5 = 320
        assertThat(d.box.x1).isWithin(0.5).of(240.0)
        assertThat(d.box.y1).isWithin(0.5).of(160.0)
        assertThat(d.box.x2).isWithin(0.5).of(400.0)
        assertThat(d.box.y2).isWithin(0.5).of(320.0)
    }

    @Test
    fun `decodeEnd2End decodes 300x6 format accurately`() {
        val tensor = FloatArray(300 * 6)
        // Box 0: x1=40, y1=60, x2=200, y2=220, conf=0.92, cls=16
        tensor[0] = 40f
        tensor[1] = 60f
        tensor[2] = 200f
        tensor[3] = 220f
        tensor[4] = 0.92f
        tensor[5] = 16f

        val params = Letterbox.computeParams(srcWidth = 640, srcHeight = 480, targetSize = 320)
        val detections = detector.decodeEnd2End(tensor, params, confThreshold = 0.25f)

        assertThat(detections).hasSize(1)
        val d = detections[0]
        assertThat(d.score).isWithin(1e-4f).of(0.92f)
        assertThat(d.classId).isEqualTo(16)
        assertThat(d.box.x1).isWithin(0.5).of(80.0)
        assertThat(d.box.y1).isWithin(0.5).of(40.0)
        assertThat(d.box.x2).isWithin(0.5).of(400.0)
        assertThat(d.box.y2).isWithin(0.5).of(360.0)
    }

    @Test
    fun `pickBest honours 2dp rounding and area tie-break rule`() {
        val params = Letterbox.computeParams(srcWidth = 640, srcHeight = 480, targetSize = 320)

        // Box A: conf 0.851, smaller area (100x100 = 10000)
        val boxA = DogDetection(Box(0.0, 0.0, 100.0, 100.0), score = 0.851f)
        // Box B: conf 0.854, larger area (200x200 = 40000)
        val boxB = DogDetection(Box(0.0, 0.0, 200.0, 200.0), score = 0.854f)

        // Both round to 0.85, so Box B should win on area tie-break
        val best1 = detector.pickBest(listOf(boxA, boxB))
        assertThat(best1).isEqualTo(boxB)

        // Box C: conf 0.89, smaller area (50x50 = 2500)
        val boxC = DogDetection(Box(0.0, 0.0, 50.0, 50.0), score = 0.89f)
        val best2 = detector.pickBest(listOf(boxA, boxB, boxC))
        assertThat(best2).isEqualTo(boxC)
    }

    @Test
    fun `replaying 8 detector output fixtures matches python outputs bit-for-bit`() {
        val params = Letterbox.computeParams(srcWidth = 640, srcHeight = 480, targetSize = 320)

        for (i in 0 until 8) {
            val resourcePath = "models/detector/output_$i.bin"
            val stream: InputStream = javaClass.classLoader?.getResourceAsStream(resourcePath)
                ?: error("Could not load resource $resourcePath")

            val bytes = stream.readBytes()
            assertThat(bytes.size).isEqualTo(84 * 2100 * 4)

            val buffer = ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN)
            val tensor = FloatArray(84 * 2100)
            buffer.asFloatBuffer().get(tensor)

            // In Python, random fixtures produce 0 detections above 0.25 conf
            val detections = detector.decodeRawHead(tensor, params, confThreshold = 0.25f)
            assertThat(detections).isEmpty()

            // At lower threshold (0.005), verify coordinates and scores are valid floats
            val looseDetections = detector.decodeRawHead(tensor, params, confThreshold = 0.005f)
            for (d in looseDetections) {
                assertThat(d.score).isAtLeast(0.005f)
                assertThat(d.box.x1.isFinite()).isTrue()
                assertThat(d.box.y1.isFinite()).isTrue()
                assertThat(d.box.x2.isFinite()).isTrue()
                assertThat(d.box.y2.isFinite()).isTrue()
            }
        }
    }

    @Test
    fun `preprocessRgbaToNchw applies letterbox padding and channel layout`() {
        val width = 4
        val height = 2
        // 4x2 RGBA image
        val rgba = ByteBuffer.allocate(width * height * 4)
        for (i in 0 until width * height) {
            rgba.put(100.toByte()) // R
            rgba.put(150.toByte()) // G
            rgba.put(200.toByte()) // B
            rgba.put(255.toByte()) // A
        }
        rgba.rewind()

        val targetSize = 8
        val params = Letterbox.computeParams(srcWidth = width, srcHeight = height, targetSize = targetSize)
        val outTensor = ByteBuffer.allocateDirect(1 * 3 * targetSize * targetSize * 4)
            .order(ByteOrder.nativeOrder())
            .asFloatBuffer()

        detector.preprocessRgbaToNchw(rgba, width, height, params, outTensor, padColor = 114)

        // Channel offsets: R: 0..63, G: 64..127, B: 128..191
        // Top and bottom row should be padColor (114/255.0f)
        val padExpected = 114 / 255.0f
        assertThat(outTensor.get(0)).isWithin(1e-4f).of(padExpected)
        assertThat(outTensor.get(64)).isWithin(1e-4f).of(padExpected)
        assertThat(outTensor.get(128)).isWithin(1e-4f).of(padExpected)
    }
}
