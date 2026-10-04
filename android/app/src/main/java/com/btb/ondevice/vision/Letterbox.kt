package com.btb.ondevice.vision

import kotlin.math.min
import kotlin.math.roundToInt

data class LetterboxParams(
    val scale: Double,
    val padLeft: Int,
    val padTop: Int,
    val targetSize: Int = 320,
) {
    /**
     * Invert letterbox coordinate transform back to full frame pixel coordinates.
     */
    fun unletterbox(x1: Double, y1: Double, x2: Double, y2: Double): List<Double> {
        return listOf(
            (x1 - padLeft) / scale,
            (y1 - padTop) / scale,
            (x2 - padLeft) / scale,
            (y2 - padTop) / scale,
        )
    }

    fun unletterbox(box: List<Double>): List<Double> {
        require(box.size >= 4) { "box must have at least 4 coordinates" }
        return unletterbox(box[0], box[1], box[2], box[3])
    }

    fun unletterbox(box: Box): Box {
        val u = unletterbox(box.x1, box.y1, box.x2, box.y2)
        return Box(u[0], u[1], u[2], u[3])
    }

    /**
     * Map full frame pixel coordinates into letterboxed space.
     */
    fun letterbox(x1: Double, y1: Double, x2: Double, y2: Double): List<Double> {
        return listOf(
            x1 * scale + padLeft,
            y1 * scale + padTop,
            x2 * scale + padLeft,
            y2 * scale + padTop,
        )
    }

    fun letterbox(box: Box): Box {
        val l = letterbox(box.x1, box.y1, box.x2, box.y2)
        return Box(l[0], l[1], l[2], l[3])
    }
}

object Letterbox {
    /**
     * Compute scale and padding offsets for letterboxing an image of dimensions
     * [srcWidth] x [srcHeight] into a square of [targetSize] x [targetSize].
     */
    fun computeParams(srcWidth: Int, srcHeight: Int, targetSize: Int = 320): LetterboxParams {
        val r = min(targetSize.toDouble() / srcHeight, targetSize.toDouble() / srcWidth)
        val newUnpadW = (srcWidth * r).roundToInt()
        val newUnpadH = (srcHeight * r).roundToInt()
        val dw = (targetSize - newUnpadW) / 2.0
        val dh = (targetSize - newUnpadH) / 2.0
        val left = (dw - 0.1).roundToInt()
        val top = (dh - 0.1).roundToInt()
        return LetterboxParams(
            scale = r,
            padLeft = left,
            padTop = top,
            targetSize = targetSize,
        )
    }
}
