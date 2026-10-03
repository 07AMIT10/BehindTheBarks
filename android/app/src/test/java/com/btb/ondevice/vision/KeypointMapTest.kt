package com.btb.ondevice.vision

import com.google.common.truth.Truth.assertThat
import org.junit.jupiter.api.Test

class KeypointMapTest {
    @Test
    fun `canonical names list contains all 19 required names`() {
        assertThat(KeypointMap.CANONICAL_NAMES).hasSize(19)
        assertThat(KeypointMap.CANONICAL_NAMES).contains("tail_base")
        assertThat(KeypointMap.CANONICAL_NAMES).contains("tail_tip")
        assertThat(KeypointMap.CANONICAL_NAMES).contains("withers")
        assertThat(KeypointMap.CANONICAL_NAMES).contains("hip")
    }

    @Test
    fun `filters out low confidence keypoints below threshold`() {
        val raw = mapOf(
            "neck_base" to listOf(100.0, 150.0, 0.29),
            "nose" to listOf(50.0, 60.0, 0.85),
            "tail_base" to listOf(200.0, 250.0, 0.31),
        )
        val canonical = KeypointMap.toCanonical(raw, confThr = 0.3)

        assertThat(canonical["withers"]).isNull()
        assertThat(canonical["nose"]).isEqualTo(listOf(50.0, 60.0, 0.85))
        assertThat(canonical["tail_base"]).isEqualTo(listOf(200.0, 250.0, 0.31))
        assertThat(canonical["tail_tip"]).isNull()
    }

    @Test
    fun `filters out NaN and infinite values`() {
        val raw = mapOf(
            "nose" to listOf(Double.NaN, 50.0, 0.9),
            "neck_base" to listOf(100.0, Double.POSITIVE_INFINITY, 0.9),
        )
        val canonical = KeypointMap.toCanonical(raw, confThr = 0.3)

        assertThat(canonical["nose"]).isNull()
        assertThat(canonical["withers"]).isNull()
    }

    @Test
    fun `ap10k mapping correctly maps root of tail to hip`() {
        val raw = mapOf(
            "Root of tail" to listOf(120.0, 220.0, 0.75),
            "Neck" to listOf(100.0, 150.0, 0.8),
            "L_Eye" to listOf(90.0, 80.0, 0.9),
        )
        val canonical = KeypointMap.toCanonical(
            raw,
            confThr = 0.3,
            mapping = KeypointMap.AP10K_TO_CANONICAL,
        )

        assertThat(canonical["hip"]).isEqualTo(listOf(120.0, 220.0, 0.75))
        assertThat(canonical["withers"]).isEqualTo(listOf(100.0, 150.0, 0.8))
        assertThat(canonical["left_eye"]).isEqualTo(listOf(90.0, 80.0, 0.9))
        assertThat(canonical["tail_tip"]).isNull()
    }
}
