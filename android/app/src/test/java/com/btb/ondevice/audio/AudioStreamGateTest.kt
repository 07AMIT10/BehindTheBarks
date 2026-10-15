package com.btb.ondevice.audio

import com.btb.ondevice.capture.ViewMode
import com.google.common.truth.Truth.assertThat
import com.google.common.truth.Truth.assertWithMessage
import org.junit.jupiter.api.Test

class AudioStreamGateTest {

    @Test
    fun `audio streams only in full with policy audio on and a listener`() {
        assertThat(AudioStreamGate.shouldStream(ViewMode.FULL, policyAudio = true, audioViewers = 1)).isTrue()
        assertThat(AudioStreamGate.shouldStream(ViewMode.FULL, policyAudio = true, audioViewers = 5)).isTrue()
    }

    @Test
    fun `truth table over every mode, policy flag and listener count`() {
        for (mode in ViewMode.values()) {
            for (policyAudio in listOf(false, true)) {
                for (viewers in listOf(-1, 0, 1, 2)) {
                    val expected = mode == ViewMode.FULL && policyAudio && viewers > 0
                    assertWithMessage("mode=$mode policyAudio=$policyAudio viewers=$viewers")
                        .that(AudioStreamGate.shouldStream(mode, policyAudio, viewers))
                        .isEqualTo(expected)
                }
            }
        }
    }

    @Test
    fun `lower modes never stream even with audio allowed and listeners`() {
        assertThat(AudioStreamGate.shouldStream(ViewMode.LIMITED, true, 3)).isFalse()
        assertThat(AudioStreamGate.shouldStream(ViewMode.PRIVACY, true, 3)).isFalse()
        assertThat(AudioStreamGate.shouldStream(ViewMode.BLACKOUT, true, 3)).isFalse()
    }

    @Test
    fun `no listener or policy without audio stops the stream`() {
        assertThat(AudioStreamGate.shouldStream(ViewMode.FULL, policyAudio = true, audioViewers = 0)).isFalse()
        assertThat(AudioStreamGate.shouldStream(ViewMode.FULL, policyAudio = false, audioViewers = 2)).isFalse()
    }
}
