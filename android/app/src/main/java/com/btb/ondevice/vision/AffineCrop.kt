package com.btb.ondevice.vision

import kotlin.math.ceil
import kotlin.math.floor
import kotlin.math.max
import kotlin.math.min

data class CropRegion(
    val ox: Int,
    val oy: Int,
    val width: Int,
    val height: Int,
) {
    val x1: Double get() = ox.toDouble()
    val y1: Double get() = oy.toDouble()
    val x2: Double get() = (ox + width).toDouble()
    val y2: Double get() = (oy + height).toDouble()
}

/**
 * 2x3 affine transformation matrix:
 * [ [m00, m01, m02],
 *   [m10, m11, m12] ]
 */
data class AffineMatrix(
    val m00: Double, val m01: Double, val m02: Double,
    val m10: Double, val m11: Double, val m12: Double,
) {
    fun transform(x: Double, y: Double): Pair<Double, Double> {
        val xt = m00 * x + m01 * y + m02
        val yt = m10 * x + m11 * y + m12
        return Pair(xt, yt)
    }

    /**
     * Compute the inverse 2x3 affine matrix.
     */
    fun inverse(): AffineMatrix {
        val det = m00 * m11 - m01 * m10
        require(kotlin.math.abs(det) > 1e-12) { "Singular affine matrix cannot be inverted" }
        val invDet = 1.0 / det
        val im00 = m11 * invDet
        val im01 = -m01 * invDet
        val im10 = -m10 * invDet
        val im11 = m00 * invDet
        val im02 = -(im00 * m02 + im01 * m12)
        val im12 = -(im10 * m02 + im11 * m12)
        return AffineMatrix(im00, im01, im02, im10, im11, im12)
    }
}

object AffineCrop {

    /**
     * Crop padded region around bbox matching backend/vision/detect.py crop_padded.
     */
    fun cropPadded(bbox: Box, pad: Double, frameW: Int, frameH: Int): CropRegion {
        val px = (bbox.x2 - bbox.x1) * pad
        val py = (bbox.y2 - bbox.y1) * pad
        val cx1 = max(0, floor(bbox.x1 - px).toInt())
        val cy1 = max(0, floor(bbox.y1 - py).toInt())
        val cx2 = min(frameW, ceil(bbox.x2 + px).toInt())
        val cy2 = min(frameH, ceil(bbox.y2 + py).toInt())
        val w = max(0, cx2 - cx1)
        val h = max(0, cy2 - cy1)
        return CropRegion(cx1, cy1, w, h)
    }

    /**
     * Compute 2x3 affine transform from 3 source points to 3 destination points.
     * Equivalent to OpenCV cv2.getAffineTransform.
     */
    fun getAffineTransform(
        src: Array<DoubleArray>,
        dst: Array<DoubleArray>,
    ): AffineMatrix {
        require(src.size == 3 && dst.size == 3) { "Requires exactly 3 src and 3 dst points" }
        val x0 = src[0][0]; val y0 = src[0][1]
        val x1 = src[1][0]; val y1 = src[1][1]
        val x2 = src[2][0]; val y2 = src[2][1]

        val u0 = dst[0][0]; val v0 = dst[0][1]
        val u1 = dst[1][0]; val v1 = dst[1][1]
        val u2 = dst[2][0]; val v2 = dst[2][1]

        val det = x0 * (y1 - y2) - y0 * (x1 - x2) + (x1 * y2 - x2 * y1)
        require(kotlin.math.abs(det) > 1e-12) { "Collinear points cannot define an affine transform" }
        val invDet = 1.0 / det

        // Inverse of matrix [[x0, y0, 1], [x1, y1, 1], [x2, y2, 1]]
        val a00 = (y1 - y2) * invDet
        val a01 = (y2 - y0) * invDet
        val a02 = (y0 - y1) * invDet

        val a10 = (x2 - x1) * invDet
        val a11 = (x0 - x2) * invDet
        val a12 = (x1 - x0) * invDet

        val a20 = (x1 * y2 - x2 * y1) * invDet
        val a21 = (x2 * y0 - x0 * y2) * invDet
        val a22 = (x0 * y1 - x1 * y0) * invDet

        val m00 = u0 * a00 + u1 * a01 + u2 * a02
        val m01 = u0 * a10 + u1 * a11 + u2 * a12
        val m02 = u0 * a20 + u1 * a21 + u2 * a22

        val m10 = v0 * a00 + v1 * a01 + v2 * a02
        val m11 = v0 * a10 + v1 * a11 + v2 * a12
        val m12 = v0 * a20 + v1 * a21 + v2 * a22

        return AffineMatrix(m00, m01, m02, m10, m11, m12)
    }

    /**
     * Compute MMPose/RTMPose style top-down affine crop matrix for a bounding box
     * with center, scale (1.25x), and aspect ratio fix.
     */
    fun computeBboxAffine(
        bbox: Box,
        dstW: Int = 256,
        dstH: Int = 256,
        scaleMultiplier: Double = 1.25,
    ): AffineMatrix {
        val cx = (bbox.x1 + bbox.x2) / 2.0
        val cy = (bbox.y1 + bbox.y2) / 2.0
        val bw = bbox.width
        val bh = bbox.height
        val maxSide = max(bw, bh) * scaleMultiplier

        val src = arrayOf(
            doubleArrayOf(cx, cy),
            doubleArrayOf(cx, cy - maxSide * 0.5),
            doubleArrayOf(cx + maxSide * 0.5, cy),
        )

        val dst = arrayOf(
            doubleArrayOf(dstW * 0.5, dstH * 0.5),
            doubleArrayOf(dstW * 0.5, 0.0),
            doubleArrayOf(dstW.toDouble(), dstH * 0.5),
        )

        return getAffineTransform(src, dst)
    }
}
