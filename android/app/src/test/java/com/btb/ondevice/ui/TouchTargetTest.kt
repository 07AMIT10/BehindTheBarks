package com.btb.ondevice.ui

import com.google.common.truth.Truth.assertThat
import org.junit.jupiter.api.Test

class TouchTargetTest {

    @Test
    fun `48 dp is never rendered one pixel short at any density`() {
        // Densities seen on the test phone (SM_E075F, 300 dpi = 1.875x) and nearby display profiles.
        for (density in listOf(1.5f, 1.75f, 1.8125f, 1.8375f, 1.875f, 2.0f, 2.25f, 2.625f, 3.0f, 3.5f)) {
            val px = TouchTarget.dpToPx(TouchTarget.MIN_DP, density)
            assertThat(px.toDouble()).isAtLeast(TouchTarget.MIN_DP * density.toDouble())
            assertThat(px - 1.0).isLessThan(TouchTarget.MIN_DP * density.toDouble()) // and not more than 1 px over
        }
    }

    @Test
    fun `the phone density gives 90 px for 48 dp`() {
        assertThat(TouchTarget.dpToPx(48, 1.875f)).isEqualTo(90)
    }

    @Test
    fun `a fractional result rounds up, not to nearest`() {
        // 48 * 1.8375 = 88.2: nearest is 88 (47 dp read back), up is 89.
        assertThat(TouchTarget.dpToPx(48, 1.8375f)).isEqualTo(89)
    }
}
