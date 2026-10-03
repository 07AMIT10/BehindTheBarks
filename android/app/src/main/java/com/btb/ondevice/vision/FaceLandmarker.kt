// Port of backend/vision/face.py
// Commit: 3683457991777e5c22e573bdb472a70202ac5ddb
package com.btb.ondevice.vision

import com.btb.ondevice.ml.ModelRunner
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.FloatBuffer
import kotlin.math.max
import kotlin.math.min

data class HeadBox(
    val x1: Double,
    val y1: Double,
    val side: Double,
)

data class FaceConfig(
    val inputSize: Int = 384,
    val cropPad: Double = 0.25,
    val minCropPx: Double = 48.0,
    val minHeadPoints: Int = 3,
    val minInsideFrac: Double = 0.9,
    val minSpread: Double = 0.25,
    val secondaryMargin: Double = 0.5,
    val insideMargin: Double = 0.05,
    val numLandmarks: Int = 46,
)

class FaceLandmarker(
    val config: FaceConfig = FaceConfig(),
    private val runner: ModelRunner? = null,
) {
    companion object {
        const val N_LANDMARKS = 46

        val HEAD_KEYPOINTS = listOf(
            "nose", "upper_jaw", "lower_jaw", "mouth_left", "mouth_right",
            "left_eye", "right_eye", "left_ear_base", "left_ear_tip",
            "right_ear_base", "right_ear_tip"
        )
        val SECONDARY_KEYPOINTS = setOf("upper_jaw", "lower_jaw", "mouth_left", "mouth_right")
        val CORE_KEYPOINTS = HEAD_KEYPOINTS.filter { it !in SECONDARY_KEYPOINTS }
        val EYES_OR_EAR_BASES = setOf("left_eye", "right_eye", "left_ear_base", "right_ear_base")
    }

    // Input buffer [1, 384, 384, 3] float32 NHWC
    private val inputBuffer: ByteBuffer by lazy {
        ByteBuffer.allocateDirect(1 * config.inputSize * config.inputSize * 3 * 4)
            .order(ByteOrder.nativeOrder())
    }

    // Output buffer [1, 92] float32
    private val outputBuffer: ByteBuffer by lazy {
        ByteBuffer.allocateDirect(1 * 2 * config.numLandmarks * 4)
            .order(ByteOrder.nativeOrder())
    }

    /**
     * Compute square head box (x1, y1, side) from canonical pose keypoints.
     */
    fun headBox(kps: Map<String, KeypointMap.Point?>): HeadBox? {
        val core = CORE_KEYPOINTS.mapNotNull { kps[it] }
        if (core.isEmpty() || EYES_OR_EAR_BASES.none { kps[it] != null }) {
            return null
        }

        var cx1 = Double.MAX_VALUE
        var cy1 = Double.MAX_VALUE
        var cx2 = -Double.MAX_VALUE
        var cy2 = -Double.MAX_VALUE
        for (pt in core) {
            if (pt.x < cx1) cx1 = pt.x
            if (pt.y < cy1) cy1 = pt.y
            if (pt.x > cx2) cx2 = pt.x
            if (pt.y > cy2) cy2 = pt.y
        }

        val mx = (cx2 - cx1) * config.secondaryMargin
        val my = (cy2 - cy1) * config.secondaryMargin

        val near = SECONDARY_KEYPOINTS.mapNotNull { name ->
            val pt = kps[name]
            if (pt != null && pt.x in (cx1 - mx)..(cx2 + mx) && pt.y in (cy1 - my)..(cy2 + my)) {
                pt
            } else null
        }

        val pts = core + near
        if (pts.size < config.minHeadPoints) {
            return null
        }

        var x1 = Double.MAX_VALUE
        var y1 = Double.MAX_VALUE
        var x2 = -Double.MAX_VALUE
        var y2 = -Double.MAX_VALUE
        for (pt in pts) {
            if (pt.x < x1) x1 = pt.x
            if (pt.y < y1) y1 = pt.y
            if (pt.x > x2) x2 = pt.x
            if (pt.y > y2) y2 = pt.y
        }

        val side = max(x2 - x1, y2 - y1) * (1.0 + 2.0 * config.cropPad)
        if (side < config.minCropPx) {
            return null
        }

        val cx = (x1 + x2) / 2.0
        val cy = (y1 + y2) / 2.0
        return HeadBox(
            x1 = cx - side / 2.0,
            y1 = cy - side / 2.0,
            side = side,
        )
    }

    /**
     * Check if normalized landmark coordinates are plausible (mostly inside crop, not collapsed).
     */
    fun isPlausible(norm: List<Pair<Double, Double>>): Boolean {
        if (norm.size != config.numLandmarks) return false
        val margin = config.insideMargin

        var insideCount = 0
        var minX = Double.MAX_VALUE
        var maxX = -Double.MAX_VALUE
        var minY = Double.MAX_VALUE
        var maxY = -Double.MAX_VALUE

        for ((x, y) in norm) {
            if (x >= -margin && x <= 1.0 + margin && y >= -margin && y <= 1.0 + margin) {
                insideCount++
            }
            if (x < minX) minX = x
            if (x > maxX) maxX = x
            if (y < minY) minY = y
            if (y > maxY) maxY = y
        }

        val insideFrac = insideCount.toDouble() / config.numLandmarks
        val spread = max(maxX - minX, maxY - minY)

        return insideFrac >= config.minInsideFrac && spread >= config.minSpread
    }

    /**
     * Decode model output [1, 92] into full-frame pixel landmark pairs.
     */
    fun decodeOutput(output: FloatArray, headBox: HeadBox): List<List<Double>>? {
        if (output.size != 2 * config.numLandmarks) return null
        for (v in output) {
            if (!v.isFinite()) return null
        }

        val norm = ArrayList<Pair<Double, Double>>(config.numLandmarks)
        for (i in 0 until config.numLandmarks) {
            norm.add(Pair(output[2 * i].toDouble(), output[2 * i + 1].toDouble()))
        }

        if (!isPlausible(norm)) return null

        return norm.map { (nx, ny) ->
            listOf(headBox.x1 + nx * headBox.side, headBox.y1 + ny * headBox.side)
        }
    }

    /**
     * Preprocess RGBA image by cropping the square head box with black borders outside frame,
     * normalized into NHWC [1, 384, 384, 3] FloatBuffer.
     */
    fun preprocessHeadCropToNhwc(
        rgba: ByteBuffer,
        frameWidth: Int,
        frameHeight: Int,
        headBox: HeadBox,
        target: FloatBuffer,
    ) {
        val size = config.inputSize
        val step = headBox.side / size.toDouble()

        for (y in 0 until size) {
            val srcY = (headBox.y1 + y * step).toInt()
            val isYValid = srcY in 0 until frameHeight
            val rowOffset = y * size * 3

            for (x in 0 until size) {
                val srcX = (headBox.x1 + x * step).toInt()
                val isXValid = srcX in 0 until frameWidth
                val pixelOffset = rowOffset + x * 3

                if (isYValid && isXValid) {
                    val rgbaIndex = (srcY * frameWidth + srcX) * 4
                    val r = (rgba.get(rgbaIndex).toInt() and 0xFF) / 255.0f
                    val g = (rgba.get(rgbaIndex + 1).toInt() and 0xFF) / 255.0f
                    val b = (rgba.get(rgbaIndex + 2).toInt() and 0xFF) / 255.0f
                    target.put(pixelOffset, r)
                    target.put(pixelOffset + 1, g)
                    target.put(pixelOffset + 2, b)
                } else {
                    target.put(pixelOffset, 0.0f)
                    target.put(pixelOffset + 1, 0.0f)
                    target.put(pixelOffset + 2, 0.0f)
                }
            }
        }
    }

    /**
     * Estimate 46 landmarks in full frame pixel coordinates or null.
     */
    fun estimate(
        rgba: ByteBuffer,
        frameWidth: Int,
        frameHeight: Int,
        kps: Map<String, KeypointMap.Point?>,
    ): List<List<Double>>? {
        val box = headBox(kps) ?: return null
        val modelRunner = runner ?: return null
        return try {
            estimateWith(modelRunner, rgba, frameWidth, frameHeight, box)
        } catch (e: Exception) {
            android.util.Log.e("FaceLandmarker", "inference failed; landmarks dropped", e)
            null
        }
    }

    private fun estimateWith(
        modelRunner: com.btb.ondevice.ml.ModelRunner,
        rgba: ByteBuffer,
        frameWidth: Int,
        frameHeight: Int,
        box: HeadBox,
    ): List<List<Double>>? {
        inputBuffer.clear()
        preprocessHeadCropToNhwc(rgba, frameWidth, frameHeight, box, inputBuffer.asFloatBuffer())

        outputBuffer.clear()
        modelRunner.run(arrayOf(inputBuffer), arrayOf(outputBuffer))

        val output = FloatArray(2 * config.numLandmarks)
        outputBuffer.rewind()
        outputBuffer.asFloatBuffer().get(output)

        return decodeOutput(output, box)
    }
}
