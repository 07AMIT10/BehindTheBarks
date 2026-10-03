package com.btb.ondevice.vision

import com.google.common.truth.Truth.assertThat
import org.junit.jupiter.api.Test

class AffineCropTest {

    @Test
    fun `cropPadded matches python crop_padded semantics`() {
        val bbox = Box(100.0, 100.0, 200.0, 200.0)
        val pad = 0.15
        val crop = AffineCrop.cropPadded(bbox, pad, frameW = 640, frameH = 480)

        // px = py = (200 - 100) * 0.15 = 15.0
        // cx1 = 100 - 15 = 85, cx2 = 200 + 15 = 215 -> w = 130
        assertThat(crop.ox).isEqualTo(85)
        assertThat(crop.oy).isEqualTo(85)
        assertThat(crop.width).isEqualTo(130)
        assertThat(crop.height).isEqualTo(130)
    }

    @Test
    fun `cropPadded clamps to frame boundaries when box near edges`() {
        val edgeBox = Box(5.0, 5.0, 50.0, 50.0)
        val crop = AffineCrop.cropPadded(edgeBox, pad = 0.2, frameW = 640, frameH = 480)
        // px = 45 * 0.2 = 9 -> cx1 = max(0, 5 - 9) = 0
        assertThat(crop.ox).isEqualTo(0)
        assertThat(crop.oy).isEqualTo(0)
        assertThat(crop.width).isEqualTo(59)
    }

    @Test
    fun `getAffineTransform solves 2x3 affine matrix matching cv2 getAffineTransform`() {
        val src = arrayOf(
            doubleArrayOf(0.0, 0.0),
            doubleArrayOf(100.0, 0.0),
            doubleArrayOf(0.0, 100.0),
        )
        // Translation by (50, 80) and 2x scaling
        val dst = arrayOf(
            doubleArrayOf(50.0, 80.0),
            doubleArrayOf(250.0, 80.0),
            doubleArrayOf(50.0, 280.0),
        )

        val matrix = AffineCrop.getAffineTransform(src, dst)
        assertThat(matrix.m00).isWithin(1e-10).of(2.0)
        assertThat(matrix.m01).isWithin(1e-10).of(0.0)
        assertThat(matrix.m02).isWithin(1e-10).of(50.0)
        assertThat(matrix.m10).isWithin(1e-10).of(0.0)
        assertThat(matrix.m11).isWithin(1e-10).of(2.0)
        assertThat(matrix.m12).isWithin(1e-10).of(80.0)

        // Point transform
        val (tx, ty) = matrix.transform(20.0, 30.0)
        assertThat(tx).isWithin(1e-10).of(20.0 * 2.0 + 50.0) // 90.0
        assertThat(ty).isWithin(1e-10).of(30.0 * 2.0 + 80.0) // 140.0

        // Inverse transform roundtrip
        val inv = matrix.inverse()
        val (origX, origY) = inv.transform(tx, ty)
        assertThat(origX).isWithin(1e-10).of(20.0)
        assertThat(origY).isWithin(1e-10).of(30.0)
    }

    @Test
    fun `computeBboxAffine maps center of bbox to center of target crop`() {
        val bbox = Box(100.0, 100.0, 300.0, 300.0)
        val matrix = AffineCrop.computeBboxAffine(bbox, dstW = 256, dstH = 256, scaleMultiplier = 1.25)

        val (cx, cy) = matrix.transform(200.0, 200.0)
        assertThat(cx).isWithin(1e-6).of(128.0)
        assertThat(cy).isWithin(1e-6).of(128.0)
    }
}
