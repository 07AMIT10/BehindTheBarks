package com.btb.ondevice.vision

import com.google.common.truth.Truth.assertThat
import org.junit.jupiter.api.Test

class LetterboxTest {

    @Test
    fun `letterbox params for landscape 640x480 match mobile spec`() {
        val params = Letterbox.computeParams(srcWidth = 640, srcHeight = 480, targetSize = 320)
        assertThat(params.scale).isEqualTo(0.5)
        assertThat(params.padLeft).isEqualTo(0)
        assertThat(params.padTop).isEqualTo(40)
        assertThat(params.targetSize).isEqualTo(320)

        // Box in letterbox coords [0, 40, 320, 280] -> full frame [0, 0, 640, 480]
        val unletterboxed = params.unletterbox(listOf(0.0, 40.0, 320.0, 280.0))
        assertThat(unletterboxed[0]).isWithin(1e-4).of(0.0)
        assertThat(unletterboxed[1]).isWithin(1e-4).of(0.0)
        assertThat(unletterboxed[2]).isWithin(1e-4).of(640.0)
        assertThat(unletterboxed[3]).isWithin(1e-4).of(480.0)

        // And reverse
        val letterboxed = params.letterbox(Box(0.0, 0.0, 640.0, 480.0))
        assertThat(letterboxed.x1).isWithin(1e-4).of(0.0)
        assertThat(letterboxed.y1).isWithin(1e-4).of(40.0)
        assertThat(letterboxed.x2).isWithin(1e-4).of(320.0)
        assertThat(letterboxed.y2).isWithin(1e-4).of(280.0)
    }

    @Test
    fun `letterbox params for portrait 480x640 support mobile portrait orientations`() {
        val params = Letterbox.computeParams(srcWidth = 480, srcHeight = 640, targetSize = 320)
        assertThat(params.scale).isEqualTo(0.5)
        assertThat(params.padLeft).isEqualTo(40)
        assertThat(params.padTop).isEqualTo(0)

        val unletterboxed = params.unletterbox(listOf(40.0, 0.0, 280.0, 320.0))
        assertThat(unletterboxed[0]).isWithin(1e-4).of(0.0)
        assertThat(unletterboxed[1]).isWithin(1e-4).of(0.0)
        assertThat(unletterboxed[2]).isWithin(1e-4).of(480.0)
        assertThat(unletterboxed[3]).isWithin(1e-4).of(640.0)
    }

    @Test
    fun `letterbox roundtrip preserves box coordinates`() {
        val params = Letterbox.computeParams(srcWidth = 1920, srcHeight = 1080, targetSize = 320)
        val original = Box(100.0, 150.0, 500.0, 600.0)
        val lb = params.letterbox(original)
        val roundtrip = params.unletterbox(lb)

        assertThat(roundtrip.x1).isWithin(1e-4).of(original.x1)
        assertThat(roundtrip.y1).isWithin(1e-4).of(original.y1)
        assertThat(roundtrip.x2).isWithin(1e-4).of(original.x2)
        assertThat(roundtrip.y2).isWithin(1e-4).of(original.y2)
    }
}
