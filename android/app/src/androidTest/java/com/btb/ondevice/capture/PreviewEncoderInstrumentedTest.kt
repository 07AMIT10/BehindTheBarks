package com.btb.ondevice.capture

import android.graphics.BitmapFactory
import android.util.Log
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import kotlin.random.Random

/**
 * The real Bitmap/JPEG path of [PreviewEncoder.encodeBitmap] on the phone, with a synthetic 640x480
 * RGBA frame (gradient plus noise). Timings are logged under tag BTB-AT.
 */
@RunWith(AndroidJUnit4::class)
class PreviewEncoderInstrumentedTest {
    private val encoder = PreviewEncoder(onEncoded = {})

    @After
    fun tearDown() {
        encoder.shutdown()
    }

    private data class Config(val maxEdge: Int, val quality: Int) {
        val label get() = "{$maxEdge,q$quality}"
    }

    private val configs = listOf(Config(640, 75), Config(640, 50), Config(320, 60))

    private val width = 640
    private val height = 480

    private fun syntheticFrame(seed: Int): ByteArray {
        val rnd = Random(seed)
        val out = ByteArray(width * height * 4)
        for (y in 0 until height) {
            for (x in 0 until width) {
                val i = (y * width + x) * 4
                val r = x * 255 / (width - 1)
                val g = y * 255 / (height - 1)
                val b = (x + y) * 255 / (width + height - 2)
                out[i] = (r + rnd.nextInt(-24, 25)).coerceIn(0, 255).toByte()
                out[i + 1] = (g + rnd.nextInt(-24, 25)).coerceIn(0, 255).toByte()
                out[i + 2] = (b + rnd.nextInt(-24, 25)).coerceIn(0, 255).toByte()
                out[i + 3] = 0xFF.toByte()
            }
        }
        return out
    }

    private fun encode(frame: ByteArray, c: Config): ByteArray {
        val jpeg = encoder.encodeBitmap(frame, width, height, 0, c.maxEdge, c.quality)
        assertNotNull("encodeBitmap returned null for ${c.label}", jpeg)
        return jpeg!!
    }

    @Test
    fun jpegHasSoiMarkerAndTheRequestedLongEdge() {
        val frame = syntheticFrame(seed = 1)
        for (c in configs) {
            val jpeg = encode(frame, c)
            assertEquals("SOI byte 0 for ${c.label}", 0xFF, jpeg[0].toInt() and 0xFF)
            assertEquals("SOI byte 1 for ${c.label}", 0xD8, jpeg[1].toInt() and 0xFF)
            val decoded = BitmapFactory.decodeByteArray(jpeg, 0, jpeg.size)
            assertNotNull("${c.label} must decode", decoded)
            assertEquals("long edge for ${c.label}", c.maxEdge, maxOf(decoded.width, decoded.height))
            Log.i("BTB-AT", "preview ${c.label}: ${jpeg.size} bytes, decoded ${decoded.width}x${decoded.height}")
            decoded.recycle()
        }
    }

    @Test
    fun quality75IsLargerThanQuality50() {
        val frame = syntheticFrame(seed = 2)
        val q75 = encode(frame, Config(640, 75)).size
        val q50 = encode(frame, Config(640, 50)).size
        Log.i("BTB-AT", "preview size at 640: q75=$q75 bytes q50=$q50 bytes")
        assertTrue("q75 ($q75 B) must be larger than q50 ($q50 B)", q75 > q50)
    }

    @Test
    fun encodeTimeOver10FramesIsUnder330msOnAverage() {
        // 330 ms is the frame budget of the 3 fps focus config. Each config is timed on 10 frames
        // after one warm-up encode (the first call pays for class loading and the codec).
        for (c in configs) {
            encode(syntheticFrame(seed = 99), c) // warm-up, not timed
            val frames = (0 until 10).map { syntheticFrame(seed = 100 + it) }
            val startNs = System.nanoTime()
            for (f in frames) encode(f, c)
            val avgMs = (System.nanoTime() - startNs) / 1_000_000.0 / frames.size
            Log.i("BTB-AT", "encode ${c.label}: avg ${"%.1f".format(avgMs)} ms over 10 frames")
            assertTrue("average encode ${c.label} = ${"%.1f".format(avgMs)} ms exceeds 330 ms", avgMs < 330.0)
        }
    }
}
