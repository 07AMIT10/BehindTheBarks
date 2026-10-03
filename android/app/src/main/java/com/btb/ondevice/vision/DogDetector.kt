// Port of backend/vision/detect.py (DogDetector) and backend/vision/mobile_runners.py (MobileDetector)
// Commit: 3683457991777e5c22e573bdb472a70202ac5ddb
package com.btb.ondevice.vision

import com.btb.ondevice.ml.ModelRunner
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.FloatBuffer
import kotlin.math.roundToInt

data class DogDetection(
    val box: Box,
    val score: Float,
    val classId: Int = 16,
)

data class DogDetectorConfig(
    val inputSize: Int = 320,
    val dogClass: Int = 16,
    val confThreshold: Float = 0.25f,
    val padColor: Int = 114,
    val kind: String = "raw_head", // "raw_head" or "end2end"
)

class DogDetector(
    val config: DogDetectorConfig = DogDetectorConfig(),
    private val runner: ModelRunner? = null,
) {
    private val inputBuffer: ByteBuffer by lazy {
        ByteBuffer.allocateDirect(1 * 3 * config.inputSize * config.inputSize * 4)
            .order(ByteOrder.nativeOrder())
    }

    private val outputBuffer: ByteBuffer by lazy {
        val size = if (config.kind == "end2end") {
            1 * 300 * 6 * 4
        } else {
            1 * 84 * 2100 * 4
        }
        ByteBuffer.allocateDirect(size).order(ByteOrder.nativeOrder())
    }

    /**
     * Decode YOLO raw head output tensor [1, 84, 2100].
     * Coordinates are cx, cy, w, h normalized to [0, 1] relative to letterbox.
     * Class 16 is dog.
     */
    fun decodeRawHead(
        tensor: FloatArray,
        params: LetterboxParams,
        confThreshold: Float = config.confThreshold,
        dogClass: Int = config.dogClass,
        inputSize: Int = config.inputSize,
    ): List<DogDetection> {
        val numAnchors = 2100
        val dogClassRow = 4 + dogClass
        val detections = mutableListOf<DogDetection>()

        for (i in 0 until numAnchors) {
            val score = tensor[dogClassRow * numAnchors + i]
            if (score >= confThreshold) {
                val cx = tensor[0 * numAnchors + i]
                val cy = tensor[1 * numAnchors + i]
                val w = tensor[2 * numAnchors + i]
                val h = tensor[3 * numAnchors + i]

                val pxX1 = (cx - w / 2.0) * inputSize
                val pxY1 = (cy - h / 2.0) * inputSize
                val pxX2 = (cx + w / 2.0) * inputSize
                val pxY2 = (cy + h / 2.0) * inputSize

                val unletterboxed = params.unletterbox(pxX1, pxY1, pxX2, pxY2)
                detections.add(
                    DogDetection(
                        box = Box(unletterboxed[0], unletterboxed[1], unletterboxed[2], unletterboxed[3]),
                        score = score,
                        classId = dogClass,
                    )
                )
            }
        }
        return detections
    }

    /**
     * Decode end2end output tensor [1, 300, 6].
     * Each entry is [x1, y1, x2, y2, conf, cls] in letterbox pixel coordinates.
     */
    fun decodeEnd2End(
        tensor: FloatArray,
        params: LetterboxParams,
        confThreshold: Float = config.confThreshold,
        dogClass: Int = config.dogClass,
    ): List<DogDetection> {
        val numBoxes = 300
        val detections = mutableListOf<DogDetection>()

        for (i in 0 until numBoxes) {
            val offset = i * 6
            val cls = tensor[offset + 5].toInt()
            val conf = tensor[offset + 4]
            if (cls == dogClass && conf >= confThreshold) {
                val x1 = tensor[offset + 0].toDouble()
                val y1 = tensor[offset + 1].toDouble()
                val x2 = tensor[offset + 2].toDouble()
                val y2 = tensor[offset + 3].toDouble()

                val unletterboxed = params.unletterbox(x1, y1, x2, y2)
                detections.add(
                    DogDetection(
                        box = Box(unletterboxed[0], unletterboxed[1], unletterboxed[2], unletterboxed[3]),
                        score = conf,
                        classId = cls,
                    )
                )
            }
        }
        return detections
    }

    /**
     * Pick the best dog detection according to Python parity selection rule:
     * Highest confidence rounded to 2 decimal places (half-to-even),
     * ties broken by larger area.
     */
    fun pickBest(detections: List<DogDetection>): DogDetection? {
        if (detections.isEmpty()) return null
        return detections.maxWithOrNull(
            compareBy<DogDetection> { (it.score * 100.0).roundToInt() }
                .thenBy { it.box.area }
        )
    }

    /**
     * Preprocess RGBA buffer directly into NCHW FloatBuffer without Bitmap allocations.
     */
    fun preprocessRgbaToNchw(
        rgba: ByteBuffer,
        width: Int,
        height: Int,
        params: LetterboxParams,
        target: FloatBuffer,
        padColor: Int = config.padColor,
    ) {
        val targetSize = params.targetSize
        val padVal = padColor / 255.0f
        val rChannelOffset = 0 * targetSize * targetSize
        val gChannelOffset = 1 * targetSize * targetSize
        val bChannelOffset = 2 * targetSize * targetSize

        val scaleInv = 1.0 / params.scale
        val padLeft = params.padLeft
        val padTop = params.padTop
        val unpadW = (width * params.scale).roundToInt()
        val unpadH = (height * params.scale).roundToInt()

        for (yOut in 0 until targetSize) {
            val isYPad = yOut < padTop || yOut >= padTop + unpadH
            val srcY = if (!isYPad) {
                (((yOut - padTop) * scaleInv).toInt()).coerceIn(0, height - 1)
            } else 0

            val rowOffset = yOut * targetSize
            for (xOut in 0 until targetSize) {
                val isXPad = xOut < padLeft || xOut >= padLeft + unpadW
                val pixelIndex = rowOffset + xOut

                if (isYPad || isXPad) {
                    target.put(rChannelOffset + pixelIndex, padVal)
                    target.put(gChannelOffset + pixelIndex, padVal)
                    target.put(bChannelOffset + pixelIndex, padVal)
                } else {
                    val srcX = (((xOut - padLeft) * scaleInv).toInt()).coerceIn(0, width - 1)
                    val rgbaIndex = (srcY * width + srcX) * 4

                    val r = (rgba.get(rgbaIndex).toInt() and 0xFF) / 255.0f
                    val g = (rgba.get(rgbaIndex + 1).toInt() and 0xFF) / 255.0f
                    val b = (rgba.get(rgbaIndex + 2).toInt() and 0xFF) / 255.0f

                    target.put(rChannelOffset + pixelIndex, r)
                    target.put(gChannelOffset + pixelIndex, g)
                    target.put(bChannelOffset + pixelIndex, b)
                }
            }
        }
    }

    /**
     * Run inference via ModelRunner on an RGBA frame.
     */
    fun detect(
        rgba: ByteBuffer,
        width: Int,
        height: Int,
        params: LetterboxParams = Letterbox.computeParams(width, height, config.inputSize),
    ): DogDetection? {
        val modelRunner = runner ?: return null
        inputBuffer.clear()
        val floatInput = inputBuffer.asFloatBuffer()
        preprocessRgbaToNchw(rgba, width, height, params, floatInput)

        outputBuffer.clear()
        modelRunner.run(arrayOf(inputBuffer), arrayOf(outputBuffer))

        outputBuffer.rewind()
        val floatOutput = FloatArray(outputBuffer.remaining() / 4)
        outputBuffer.asFloatBuffer().get(floatOutput)

        val detections = if (config.kind == "end2end") {
            decodeEnd2End(floatOutput, params)
        } else {
            decodeRawHead(floatOutput, params)
        }
        return pickBest(detections)
    }
}
