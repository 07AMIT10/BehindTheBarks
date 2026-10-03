package com.btb.ondevice.capture

import com.google.common.truth.Truth.assertThat
import org.junit.jupiter.api.Test
import java.nio.ByteBuffer
import java.nio.ByteOrder

class CameraSourceRotationTest {

    @Test
    fun `extractUprightRgba rotates 90 degrees clockwise`() {
        val width = 4
        val height = 2
        val buffer = ByteBuffer.allocateDirect(width * height * 4).order(ByteOrder.nativeOrder())
        val intBuf = buffer.asIntBuffer()

        // Row 0: [1, 2, 3, 4]
        // Row 1: [5, 6, 7, 8]
        intBuf.put(0, 1)
        intBuf.put(1, 2)
        intBuf.put(2, 3)
        intBuf.put(3, 4)
        intBuf.put(4, 5)
        intBuf.put(5, 6)
        intBuf.put(6, 7)
        intBuf.put(7, 8)

        val outBytes = ByteArray(width * height * 4)
        val (outW, outH) = CameraSource.extractUprightRgba(
            buffer = buffer,
            width = width,
            height = height,
            rowStride = width * 4,
            pixelStride = 4,
            rotationDegrees = 90,
            outBytes = outBytes,
        )

        assertThat(outW).isEqualTo(2)
        assertThat(outH).isEqualTo(4)

        val outInts = ByteBuffer.wrap(outBytes).order(ByteOrder.nativeOrder()).asIntBuffer()
        // 90 deg clockwise rotation:
        // Col 0 becomes Row 0 reversed: [5, 1]
        // Col 1 becomes Row 1 reversed: [6, 2]
        // Col 2 becomes Row 2 reversed: [7, 3]
        // Col 3 becomes Row 3 reversed: [8, 4]
        val result = IntArray(8)
        outInts.get(result)
        assertThat(result).isEqualTo(intArrayOf(5, 1, 6, 2, 7, 3, 8, 4))
    }

    @Test
    fun `extractUprightRgba passes through 0 degrees unrotated`() {
        val width = 2
        val height = 2
        val buffer = ByteBuffer.allocateDirect(width * height * 4).order(ByteOrder.nativeOrder())
        val intBuf = buffer.asIntBuffer()
        intBuf.put(0, 10)
        intBuf.put(1, 20)
        intBuf.put(2, 30)
        intBuf.put(3, 40)

        val outBytes = ByteArray(width * height * 4)
        val (outW, outH) = CameraSource.extractUprightRgba(
            buffer = buffer,
            width = width,
            height = height,
            rowStride = width * 4,
            pixelStride = 4,
            rotationDegrees = 0,
            outBytes = outBytes,
        )

        assertThat(outW).isEqualTo(2)
        assertThat(outH).isEqualTo(2)

        val outInts = ByteBuffer.wrap(outBytes).order(ByteOrder.nativeOrder()).asIntBuffer()
        val result = IntArray(4)
        outInts.get(result)
        assertThat(result).isEqualTo(intArrayOf(10, 20, 30, 40))
    }

    @Test
    fun `extractUprightRgba handles rowStride with padding`() {
        val width = 2
        val height = 2
        val rowStride = 16 // 4 pixels per row instead of 2 (8 bytes padding)
        val buffer = ByteBuffer.allocateDirect(height * rowStride).order(ByteOrder.nativeOrder())
        val intBuf = buffer.asIntBuffer()
        // Row 0: [1, 2, pad, pad]
        intBuf.put(0, 1)
        intBuf.put(1, 2)
        // Row 1: [3, 4, pad, pad]
        intBuf.put(4, 3)
        intBuf.put(5, 4)

        val outBytes = ByteArray(width * height * 4)
        val (outW, outH) = CameraSource.extractUprightRgba(
            buffer = buffer,
            width = width,
            height = height,
            rowStride = rowStride,
            pixelStride = 4,
            rotationDegrees = 90,
            outBytes = outBytes,
        )

        assertThat(outW).isEqualTo(2)
        assertThat(outH).isEqualTo(2)

        val outInts = ByteBuffer.wrap(outBytes).order(ByteOrder.nativeOrder()).asIntBuffer()
        val result = IntArray(4)
        outInts.get(result)
        // 90 deg clockwise:
        // Col 0: [3, 1]
        // Col 1: [4, 2]
        assertThat(result).isEqualTo(intArrayOf(3, 1, 4, 2))
    }
}
