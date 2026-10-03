// Port of backend/vision/pose.py and backend/vision/mobile_runners.py (MobilePose)
// Commit: 3683457991777e5c22e573bdb472a70202ac5ddb
package com.btb.ondevice.vision

import com.btb.ondevice.ml.ModelRunner
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.FloatBuffer

class PoseEstimator(
    val config: PoseConfig = PoseConfig(),
    private val runner: ModelRunner? = null,
) {
    // Input is [1, 3, 256, 256] float32
    private val inputBuffer: ByteBuffer by lazy {
        ByteBuffer.allocateDirect(1 * 3 * config.inputSize * config.inputSize * 4)
            .order(ByteOrder.nativeOrder())
    }

    // Output is simcc_x [1, 17, 512] and simcc_y [1, 17, 512]
    private val outputXBuffer: ByteBuffer by lazy {
        ByteBuffer.allocateDirect(1 * 17 * 512 * 4).order(ByteOrder.nativeOrder())
    }
    private val outputYBuffer: ByteBuffer by lazy {
        ByteBuffer.allocateDirect(1 * 17 * 512 * 4).order(ByteOrder.nativeOrder())
    }

    private val imagenetMean = floatArrayOf(0.485f, 0.456f, 0.406f)
    private val imagenetStd = floatArrayOf(0.229f, 0.224f, 0.225f)

    /**
     * Preprocess RGBA crop into NCHW normalized [1, 3, 256, 256] buffer.
     */
    fun preprocessCropToNchw(
        rgba: ByteBuffer,
        frameWidth: Int,
        frameHeight: Int,
        crop: CropRegion,
        target: FloatBuffer,
    ) {
        val size = config.inputSize
        val sx = crop.width.toDouble() / size
        val sy = crop.height.toDouble() / size

        val rOffset = 0 * size * size
        val gOffset = 1 * size * size
        val bOffset = 2 * size * size

        for (y in 0 until size) {
            val srcY = (crop.oy + y * sy).toInt().coerceIn(0, frameHeight - 1)
            val rowOffset = y * size
            for (x in 0 until size) {
                val srcX = (crop.ox + x * sx).toInt().coerceIn(0, frameWidth - 1)
                val pixelIndex = (srcY * frameWidth + srcX) * 4

                val rNorm = ((rgba.get(pixelIndex).toInt() and 0xFF) / 255.0f - imagenetMean[0]) / imagenetStd[0]
                val gNorm = ((rgba.get(pixelIndex + 1).toInt() and 0xFF) / 255.0f - imagenetMean[1]) / imagenetStd[1]
                val bNorm = ((rgba.get(pixelIndex + 2).toInt() and 0xFF) / 255.0f - imagenetMean[2]) / imagenetStd[2]

                val outIdx = rowOffset + x
                target.put(rOffset + outIdx, rNorm)
                target.put(gOffset + outIdx, gNorm)
                target.put(bOffset + outIdx, bNorm)
            }
        }
    }

    /**
     * Run pose estimation on a dog bounding box in frame.
     */
    fun estimate(
        rgba: ByteBuffer,
        frameWidth: Int,
        frameHeight: Int,
        bbox: Box,
    ): Map<String, KeypointMap.Point?> {
        val crop = AffineCrop.cropPadded(bbox, config.cropPad, frameWidth, frameHeight)
        if (crop.width < 2 || crop.height < 2) {
            return KeypointMap.CANONICAL_NAMES.associateWith { null }
        }

        val modelRunner = runner ?: return KeypointMap.CANONICAL_NAMES.associateWith { null }
        inputBuffer.clear()
        preprocessCropToNchw(rgba, frameWidth, frameHeight, crop, inputBuffer.asFloatBuffer())

        outputXBuffer.clear()
        outputYBuffer.clear()
        modelRunner.run(arrayOf(inputBuffer), arrayOf(outputXBuffer, outputYBuffer))

        val simccX = FloatArray(17 * 512)
        val simccY = FloatArray(17 * 512)
        outputXBuffer.rewind()
        outputYBuffer.rewind()
        outputXBuffer.asFloatBuffer().get(simccX)
        outputYBuffer.asFloatBuffer().get(simccY)

        return SimCCDecoder.decode(simccX, simccY, crop, config)
    }
}
