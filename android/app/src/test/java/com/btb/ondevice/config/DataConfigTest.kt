package com.btb.ondevice.config

import com.google.common.truth.Truth.assertThat
import org.junit.jupiter.api.Test

class DataConfigTest {
    @Test
    fun `loads and decodes config_data json asset correctly`() {
        val stream = javaClass.getResourceAsStream("/config_data.json")
        assertThat(stream).isNotNull()
        val jsonStr = stream!!.bufferedReader().use { it.readText() }
        val config = DataConfig.fromJson(jsonStr)

        assertThat(config.fps).isEqualTo(15.0)
        assertThat(config.detectHz).isEqualTo(3.0)
        assertThat(config.poseHz).isEqualTo(6.0)
        assertThat(config.faceHz).isEqualTo(1.0)
        assertThat(config.keypointConfThreshold).isEqualTo(0.3)

        // Detect
        assertThat(config.detect.backend).isEqualTo("tflite")
        assertThat(config.detect.conf).isEqualTo(0.25)
        assertThat(config.detect.imgsz).isEqualTo(320)
        assertThat(config.detect.cropPad).isEqualTo(0.15)

        // Pose
        assertThat(config.pose.backend).isEqualTo("tflite")
        assertThat(config.pose.cropPad).isEqualTo(0.15)

        // Features
        assertThat(config.features.wag.source).isEqualTo("both")
        assertThat(config.features.wag.minHz).isEqualTo(1.0)
        assertThat(config.features.wag.maxHz).isEqualTo(8.0)

        // Rules
        assertThat(config.rules.disinterestedS).isEqualTo(5.0)
        assertThat(config.rules.thresholds["motion_low"]).isEqualTo(0.035)
        assertThat(config.rules.thresholds["motion_high"]).isEqualTo(0.8)
        assertThat(config.rules.weights["excited"]?.get("wag_fast")).isEqualTo(1.5)
        assertThat(config.rules.eventBonus["excited"]?.get("treat")).isEqualTo(0.25)
        assertThat(config.rules.suppressAggressiveWithoutGrowl).isFalse()
        assertThat(config.rules.ignoreEatingConfounds).isFalse()
    }
}
