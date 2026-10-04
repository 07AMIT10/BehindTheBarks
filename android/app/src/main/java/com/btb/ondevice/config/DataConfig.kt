package com.btb.ondevice.config

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

/**
 * On-device data perception configuration, mirroring config.yaml + config.mobile.yaml data section.
 * Source of truth exported to assets/config_data.json by scripts/export_data_config.py.
 */
@Serializable
data class DataConfig(
    val fps: Double = 15.0,
    @SerialName("detect_hz") val detectHz: Double = 3.0,
    @SerialName("pose_hz") val poseHz: Double = 6.0,
    @SerialName("face_hz") val faceHz: Double = 1.0,
    @SerialName("keypoint_conf_threshold") val keypointConfThreshold: Double = 0.3,
    @SerialName("feeding_zone") val feedingZone: List<List<Double>> = emptyList(),
    val detect: DetectConfig = DetectConfig(),
    val pose: PoseConfig = PoseConfig(),
    val face: FaceConfig = FaceConfig(),
    val features: FeaturesConfig = FeaturesConfig(),
    val audio: AudioConfig = AudioConfig(),
    val rules: RulesConfig = RulesConfig(),
) {
    companion object {
        private val json = Json {
            ignoreUnknownKeys = true
            explicitNulls = false
        }

        fun fromJson(jsonStr: String): DataConfig = json.decodeFromString(jsonStr)
    }
}

@Serializable
data class DetectConfig(
    val model: String = "",
    val backend: String? = null,
    val conf: Double = 0.25,
    val imgsz: Int = 320,
    @SerialName("ema_alpha") val emaAlpha: Double = 0.5,
    @SerialName("ema_reset_misses") val emaResetMisses: Int = 8,
    @SerialName("ema_reset_s") val emaResetS: Double = 1.0,
    @SerialName("crop_pad") val cropPad: Double = 0.15,
    @SerialName("zone_anchor") val zoneAnchor: String = "bottom_center",
)

@Serializable
data class PoseConfig(
    val model: String = "",
    val backend: String? = null,
    val superanimal: String? = null,
    val device: String? = null,
    @SerialName("crop_pad") val cropPad: Double = 0.15,
)

@Serializable
data class FaceConfig(
    val model: String = "",
    val threads: Int = 4,
    @SerialName("crop_pad") val cropPad: Double = 0.25,
    @SerialName("min_crop_px") val minCropPx: Int = 48,
    @SerialName("min_head_points") val minHeadPoints: Int = 3,
    @SerialName("min_inside_frac") val minInsideFrac: Double = 0.9,
    @SerialName("min_spread") val minSpread: Double = 0.25,
)

@Serializable
data class WagConfig(
    val source: String = "both",
    @SerialName("min_hz") val minHz: Double = 1.0,
    @SerialName("max_hz") val maxHz: Double = 8.0,
)

@Serializable
data class FeaturesConfig(
    @SerialName("window_s") val windowS: Double = 3.0,
    @SerialName("gap_reset_s") val gapResetS: Double = 2.0,
    @SerialName("tail_height_scale") val tailHeightScale: Double = 0.4,
    val wag: WagConfig = WagConfig(),
    @SerialName("wag_min_std") val wagMinStd: Double = 0.03,
    @SerialName("wag_min_peak_ratio") val wagMinPeakRatio: Double = 0.3,
    @SerialName("ear_up_min") val earUpMin: Double = 0.6,
    @SerialName("ear_back_cos") val earBackCos: Double = 0.85,
    @SerialName("mouth_closed") val mouthClosed: Double = 0.03,
    @SerialName("mouth_full") val mouthFull: Double = 0.25,
    @SerialName("baseline_s") val baselineS: Double = 30.0,
    @SerialName("lowering_deadband") val loweringDeadband: Double = 0.05,
    @SerialName("lowering_full") val loweringFull: Double = 0.4,
    @SerialName("motion_floor") val motionFloor: Double = 0.2,
    @SerialName("motion_full") val motionFull: Double = 1.0,
    val smooth: Map<String, Double> = emptyMap(),
)

@Serializable
data class AudioConfig(
    val model: String? = null,
    val backend: String? = null,
    @SerialName("model_dir") val modelDir: String? = null,
    @SerialName("hop_s") val hopS: Double = 0.48,
    val threshold: Double = 0.3,
    @SerialName("silence_rms") val silenceRms: Double = 0.005,
    @SerialName("debounce_s") val debounceS: Double = 1.0,
    @SerialName("max_gap_s") val maxGapS: Double = 0.5,
    @SerialName("label_map") val labelMap: Map<String, List<String>> = emptyMap(),
)

@Serializable
data class RulesConfig(
    @SerialName("window_s") val windowS: Double = 3.0,
    @SerialName("treat_window_s") val treatWindowS: Double = 10.0,
    @SerialName("hysteresis_s") val hysteresisS: Double = 1.5,
    @SerialName("no_dog_unknown_s") val noDogUnknownS: Double = 2.0,
    @SerialName("disinterested_s") val disinterestedS: Double = 5.0,
    @SerialName("disinterested_grace_s") val disinterestedGraceS: Double = 1.0,
    @SerialName("disinterested_min_score") val disinterestedMinScore: Double = 0.7,
    @SerialName("aggressive_max_conf_without_growl") val aggressiveMaxConfWithoutGrowl: Double = 0.5,
    @SerialName("growl_min_score") val growlMinScore: Double = 0.3,
    @SerialName("min_score") val minScore: Double = 0.3,
    @SerialName("min_coverage") val minCoverage: Double = 0.5,
    val thresholds: Map<String, Double> = emptyMap(),
    val softness: Map<String, Double> = emptyMap(),
    val weights: Map<String, Map<String, Double>> = emptyMap(),
    @SerialName("event_bonus") val eventBonus: Map<String, Map<String, Double>> = emptyMap(),
    @SerialName("event_penalty") val eventPenalty: Map<String, Map<String, Double>> = emptyMap(),
    @SerialName("suppress_aggressive_without_growl") val suppressAggressiveWithoutGrowl: Boolean = false,
    @SerialName("ignore_eating_confounds") val ignoreEatingConfounds: Boolean = false,
)
