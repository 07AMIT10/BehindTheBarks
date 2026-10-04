// Port of backend/vision/mobile_runners.py (MobilePose) and SimCC decoder
// Commit: 3683457991777e5c22e573bdb472a70202ac5ddb
package com.btb.ondevice.vision

import kotlin.math.max
import kotlin.math.min

data class PoseConfig(
    val inputSize: Int = 256,
    val splitRatio: Double = 2.0,
    val confThreshold: Double = 0.3,
    val outsideMarginFrac: Double = 0.05,
    val cropPad: Double = 0.15,
    val bboxScale: Double = 1.25,
    val prepMode: String = "stretch",
    val keypointNames: List<String> = listOf(
        "L_Eye", "R_Eye", "Nose", "Neck", "Root of tail",
        "L_Shoulder", "L_Elbow", "L_F_Paw", "R_Shoulder", "R_Elbow", "R_F_Paw",
        "L_Hip", "L_Knee", "L_B_Paw", "R_Hip", "R_Knee", "R_B_Paw"
    ),
    val keypointMapping: Map<String, String> = KeypointMap.AP10K_TO_CANONICAL,
)

object SimCCDecoder {

    /**
     * Decode SimCC x and y 1D heatmap vectors into full-frame pixel coordinates
     * matching MobilePose.estimate in backend/vision/mobile_runners.py.
     *
     * @param simccX Flat array of shape [numKeypoints * numBins] (e.g. 17 * 512)
     * @param simccY Flat array of shape [numKeypoints * numBins] (e.g. 17 * 512)
     * @param crop Bounding box crop region with full-frame offsets (ox, oy)
     * @param config Model configuration and canonical mapping rules
     */
    fun decode(
        simccX: FloatArray,
        simccY: FloatArray,
        crop: CropRegion,
        config: PoseConfig = PoseConfig(),
    ): Map<String, KeypointMap.Point?> {
        val numKps = config.keypointNames.size
        val numBins = simccX.size / numKps
        val splitRatio = config.splitRatio
        val inputSize = config.inputSize.toDouble()

        val sx = crop.width / inputSize
        val sy = crop.height / inputSize

        val mx = config.outsideMarginFrac * crop.width
        val my = config.outsideMarginFrac * crop.height

        val result = mutableMapOf<String, KeypointMap.Point?>()
        for (name in KeypointMap.CANONICAL_NAMES) {
            result[name] = null
        }

        for (i in 0 until numKps) {
            val rawName = config.keypointNames[i]
            val canonicalName = config.keypointMapping[rawName] ?: continue

            val rowOffset = i * numBins
            var bestBinX = 0
            var maxValX = -Float.MAX_VALUE
            var bestBinY = 0
            var maxValY = -Float.MAX_VALUE

            for (b in 0 until numBins) {
                val vx = simccX[rowOffset + b]
                if (vx > maxValX) {
                    maxValX = vx
                    bestBinX = b
                }
                val vy = simccY[rowOffset + b]
                if (vy > maxValY) {
                    maxValY = vy
                    bestBinY = b
                }
            }

            val xsIn = bestBinX / splitRatio
            val ysIn = bestBinY / splitRatio
            val score = max(0.0, min(1.0, (maxValX * maxValY).toDouble()))

            val cx = xsIn * sx
            val cy = ysIn * sy

            if (score < config.confThreshold ||
                cx < -mx || cx > crop.width + mx ||
                cy < -my || cy > crop.height + my
            ) {
                continue
            }

            val fullX = cx + crop.ox
            val fullY = cy + crop.oy
            result[canonicalName] = KeypointMap.Point(fullX, fullY, score)
        }

        return result
    }
}
