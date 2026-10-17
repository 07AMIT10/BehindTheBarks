package com.btb.ondevice.ui

import android.content.Context
import android.view.View
import android.widget.LinearLayout

/**
 * A vertical column that never gets wider than [maxWidthPx]. Wrapped in a centred parent, it keeps
 * the controls readable on tablets instead of stretching them edge to edge.
 */
internal class CappedWidthLinearLayout(context: Context, private val maxWidthPx: Int) : LinearLayout(context) {

    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        val mode = View.MeasureSpec.getMode(widthMeasureSpec)
        val size = View.MeasureSpec.getSize(widthMeasureSpec)
        val capped = if (mode != View.MeasureSpec.UNSPECIFIED && size > maxWidthPx) {
            View.MeasureSpec.makeMeasureSpec(maxWidthPx, mode)
        } else {
            widthMeasureSpec
        }
        super.onMeasure(capped, heightMeasureSpec)
    }
}
