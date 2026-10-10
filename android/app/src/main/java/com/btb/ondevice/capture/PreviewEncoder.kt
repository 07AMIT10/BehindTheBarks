package com.btb.ondevice.capture

import android.graphics.Bitmap
import android.graphics.Matrix
import com.btb.ondevice.pipeline.DegradeLevel
import java.io.ByteArrayOutputStream
import java.nio.ByteBuffer
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference
import kotlin.math.roundToInt

data class PreviewConfig(
    val fps: Double = 3.0,
    val maxEdge: Int = 640,
    val quality: Int = 70,
) {
    fun intervalMs(): Long = if (fps <= 0.0) Long.MAX_VALUE else (1000.0 / fps).toLong()
}

/**
 * Adaptive quality ladder (T5.2):
 * Steps down one rung when ThermalGovernor level degrades or backpressure drops exceed 20% in 10s.
 * Steps back up after 30s clean.
 */
class PreviewLadder {
    companion object {
        val RUNGS = listOf(
            PreviewConfig(fps = 5.0, maxEdge = 640, quality = 75),
            PreviewConfig(fps = 3.0, maxEdge = 640, quality = 65),
            PreviewConfig(fps = 2.0, maxEdge = 480, quality = 60),
            PreviewConfig(fps = 1.0, maxEdge = 320, quality = 60),
        )
    }

    var currentRungIndex: Int = 1
        private set

    private var lastDegradeMs: Long = 0L

    fun currentConfig(): PreviewConfig = RUNGS[currentRungIndex]

    fun onFeedback(
        degradeLevel: DegradeLevel,
        recentBackpressureDropRatio: Double,
        nowMs: Long
    ): PreviewConfig {
        val shouldStepDown = degradeLevel > DegradeLevel.L0_NORMAL || recentBackpressureDropRatio > 0.20
        if (shouldStepDown) {
            if (currentRungIndex < RUNGS.size - 1) {
                currentRungIndex++
            }
            lastDegradeMs = nowMs
        } else if (nowMs - lastDegradeMs >= 30_000L) {
            if (currentRungIndex > 0) {
                currentRungIndex--
                lastDegradeMs = nowMs
            }
        }
        return currentConfig()
    }

    fun reset(rungIndex: Int = 1) {
        currentRungIndex = rungIndex.coerceIn(0, RUNGS.size - 1)
        lastDegradeMs = 0L
    }
}

/**
 * Decoupled preview encoder (T5.2):
 * Takes RGBA frames on offer(), buffers the newest frame, downscales and encodes JPEG
 * on a dedicated single-thread worker. Inference never pays for encoding and preview
 * frames are not dropped when inference is busy.
 */
class PreviewEncoder(
    private val executor: ExecutorService = Executors.newSingleThreadExecutor { r ->
        Thread(r, "preview-encoder").apply { priority = Thread.NORM_PRIORITY - 1 }
    },
    private val clock: () -> Long = System::currentTimeMillis,
    private val encodeFn: ((ByteArray, Int, Int, Int, Int, Int) -> ByteArray?)? = null,
    private val onEncoded: (ByteArray) -> Unit,
) {
    data class FrameToEncode(
        val rgba: ByteArray,
        val width: Int,
        val height: Int,
        val rotation: Int,
    )

    @Volatile
    var config: PreviewConfig = PreviewConfig(fps = 3.0, maxEdge = 640, quality = 70)

    @Volatile
    var enabled: Boolean = true

    private val latestFrame = AtomicReference<FrameToEncode?>(null)
    private val isEncoding = AtomicBoolean(false)
    private var lastEncodeStartMs = 0L
    private val reusedStream = ByteArrayOutputStream(64 * 1024)

    fun offer(rgba: ByteArray, width: Int, height: Int, rotation: Int): Boolean {
        if (!enabled || config.fps <= 0.0) return false
        val now = clock()
        val interval = config.intervalMs()
        if (now - lastEncodeStartMs < interval) {
            return false
        }

        latestFrame.set(FrameToEncode(rgba, width, height, rotation))
        lastEncodeStartMs = now

        if (isEncoding.compareAndSet(false, true)) {
            try {
                executor.execute {
                    drainAndEncode()
                }
            } catch (_: Exception) {
                isEncoding.set(false)
            }
        }
        return true
    }

    private fun drainAndEncode() {
        try {
            while (true) {
                val frame = latestFrame.getAndSet(null) ?: break
                val cfg = config
                val jpeg = try {
                    if (encodeFn != null) {
                        encodeFn.invoke(frame.rgba, frame.width, frame.height, frame.rotation, cfg.maxEdge, cfg.quality)
                    } else {
                        encodeBitmap(frame.rgba, frame.width, frame.height, frame.rotation, cfg.maxEdge, cfg.quality)
                    }
                } catch (t: Throwable) {
                    android.util.Log.e("PreviewEncoder", "Encode error: ${t.message}", t)
                    null
                }

                if (jpeg != null && jpeg.isNotEmpty()) {
                    try {
                        onEncoded(jpeg)
                    } catch (t: Throwable) {
                        android.util.Log.e("PreviewEncoder", "Error delivering encoded preview: ${t.message}", t)
                    }
                }
            }
        } finally {
            isEncoding.set(false)
            if (latestFrame.get() != null && isEncoding.compareAndSet(false, true)) {
                try {
                    executor.execute { drainAndEncode() }
                } catch (_: Exception) {
                    isEncoding.set(false)
                }
            }
        }
    }

    fun encodeBitmap(
        rgba: ByteArray,
        width: Int,
        height: Int,
        rotationDegrees: Int,
        maxEdge: Int,
        quality: Int,
    ): ByteArray? {
        if (width < 2 || height < 2 || rgba.size < width * height * 4) return null
        return try {
            val full = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888)
            full.copyPixelsFromBuffer(ByteBuffer.wrap(rgba))
            val targetW: Int
            val targetH: Int
            val edge = maxEdge.coerceIn(64, 1280)
            if (width >= height) {
                targetW = edge
                targetH = (edge.toFloat() * height / width).roundToInt().coerceAtLeast(2)
            } else {
                targetW = (edge.toFloat() * width / height).roundToInt().coerceAtLeast(2)
                targetH = edge
            }
            val scaled = Bitmap.createScaledBitmap(full, targetW, targetH, true)
            full.recycle()
            val upright = if (rotationDegrees != 0) {
                val m = Matrix().apply { postRotate(rotationDegrees.toFloat()) }
                Bitmap.createBitmap(scaled, 0, 0, scaled.width, scaled.height, m, true)
                    .also { scaled.recycle() }
            } else {
                scaled
            }
            synchronized(reusedStream) {
                reusedStream.reset()
                upright.compress(Bitmap.CompressFormat.JPEG, quality.coerceIn(20, 95), reusedStream)
                upright.recycle()
                reusedStream.toByteArray().takeIf { it.isNotEmpty() }
            }
        } catch (t: Throwable) {
            android.util.Log.e("PreviewEncoder", "encodeBitmap error: ${t.message}", t)
            null
        }
    }

    fun shutdown() {
        enabled = false
        executor.shutdownNow()
    }
}
