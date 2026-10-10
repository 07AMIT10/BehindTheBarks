package com.btb.ondevice.ui

import kotlin.math.ceil

/** The Material touch target: every tappable control is at least this many dp in both directions. */
object TouchTarget {
    const val MIN_DP = 48

    /**
     * dp to px, rounded UP. Rounding to nearest can land one pixel short of the target (48 dp at 1.8375x is
     * 88.2 px; nearest gives 88 px, which the uiautomator dump reads as 47 dp), so the px value is never below it.
     */
    fun dpToPx(dp: Int, density: Float): Int = ceil(dp * density.toDouble()).toInt()
}
