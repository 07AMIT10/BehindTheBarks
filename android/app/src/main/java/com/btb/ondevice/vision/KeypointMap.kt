// Port of backend/vision/keypoint_map.py
// Commit: 361a24f707508c36cde8a5046db38b75deab8025
package com.btb.ondevice.vision

/**
 * Maps model-specific keypoint names to our canonical 19 names.
 * Downstream code (face, features, rules, dashboard overlay) only ever sees CANONICAL_NAMES.
 */
object KeypointMap {
    data class Point(val x: Double, val y: Double, val conf: Double) {
        fun toList(): List<Double> = listOf(x, y, conf)
    }

    val CANONICAL_NAMES: List<String> = listOf(
        "nose",
        "upper_jaw",
        "lower_jaw",
        "mouth_left",
        "mouth_right",
        "left_eye",
        "right_eye",
        "left_ear_base",
        "left_ear_tip",
        "right_ear_base",
        "right_ear_tip",
        "withers",
        "hip",
        "tail_base",
        "tail_tip",
        "left_front_paw",
        "right_front_paw",
        "left_back_paw",
        "right_back_paw",
    )

    val SUPERANIMAL_TO_CANONICAL: Map<String, String> = mapOf(
        "nose" to "nose",
        "upper_jaw" to "upper_jaw",
        "lower_jaw" to "lower_jaw",
        "mouth_end_left" to "mouth_left",
        "mouth_end_right" to "mouth_right",
        "left_eye" to "left_eye",
        "right_eye" to "right_eye",
        "left_earbase" to "left_ear_base",
        "left_earend" to "left_ear_tip",
        "right_earbase" to "right_ear_base",
        "right_earend" to "right_ear_tip",
        "neck_base" to "withers",
        "back_end" to "hip",
        "tail_base" to "tail_base",
        "tail_end" to "tail_tip",
        "front_left_paw" to "left_front_paw",
        "front_right_paw" to "right_front_paw",
        "back_left_paw" to "left_back_paw",
        "back_right_paw" to "right_back_paw",
    )

    val AP10K_TO_CANONICAL: Map<String, String> = mapOf(
        "L_Eye" to "left_eye",
        "R_Eye" to "right_eye",
        "Nose" to "nose",
        "Neck" to "withers",
        "Root of tail" to "hip",
        "L_F_Paw" to "left_front_paw",
        "R_F_Paw" to "right_front_paw",
        "L_B_Paw" to "left_back_paw",
        "R_B_Paw" to "right_back_paw",
    )

    val SKELETON: List<Pair<String, String>> = listOf(
        "nose" to "upper_jaw",
        "upper_jaw" to "lower_jaw",
        "left_eye" to "nose",
        "right_eye" to "nose",
        "left_ear_base" to "left_ear_tip",
        "right_ear_base" to "right_ear_tip",
        "nose" to "withers",
        "withers" to "hip",
        "hip" to "tail_base",
        "tail_base" to "tail_tip",
        "withers" to "left_front_paw",
        "withers" to "right_front_paw",
        "hip" to "left_back_paw",
        "hip" to "right_back_paw",
    )

    /**
     * Rename raw model keypoints to canonical names.
     * Every canonical name is present in the result.
     * Points missing, NaN, infinite, or below confThr become null.
     */
    fun toCanonical(
        raw: Map<String, List<Double>>,
        confThr: Double = 0.3,
        mapping: Map<String, String> = SUPERANIMAL_TO_CANONICAL,
    ): Map<String, List<Double>?> {
        val out = mutableMapOf<String, List<Double>?>()
        for (name in CANONICAL_NAMES) {
            out[name] = null
        }
        for ((rawName, canon) in mapping) {
            val p = raw[rawName] ?: continue
            if (p.size < 3) continue
            val x = p[0]
            val y = p[1]
            val c = p[2]
            if (c >= confThr && !x.isNaN() && !y.isNaN() && !x.isInfinite() && !y.isInfinite()) {
                out[canon] = listOf(x, y, c)
            }
        }
        return out
    }
}
