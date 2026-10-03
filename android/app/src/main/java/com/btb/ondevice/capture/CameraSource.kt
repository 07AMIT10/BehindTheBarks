package com.btb.ondevice.capture

import android.content.Context
import android.os.SystemClock
import android.util.Size
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import androidx.camera.core.Preview
import androidx.camera.core.resolutionselector.ResolutionSelector
import androidx.camera.core.resolutionselector.ResolutionStrategy
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.core.content.ContextCompat
import androidx.lifecycle.LifecycleOwner
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors

typealias FrameCallback = (rgbaBytes: ByteArray, width: Int, height: Int, rotationDegrees: Int, timestampS: Double) -> Unit

class CameraSource(
    private val context: Context,
    private val lifecycleOwner: LifecycleOwner,
    private val targetWidth: Int = 640,
    private val targetHeight: Int = 480,
    private val targetFps: Int = 15,
    private val frameCallback: FrameCallback,
) {
    private var cameraProvider: ProcessCameraProvider? = null
    private var imageAnalysis: ImageAnalysis? = null
    private var previewUseCase: Preview? = null
    private val executor: ExecutorService = Executors.newSingleThreadExecutor()

    private val minFrameIntervalNs = (1_000_000_000.0 / targetFps).toLong()
    private var lastFrameTimeNs = 0L

    // boot-to-epoch offset for timestamp conversion
    private val bootToEpochOffsetS = (System.currentTimeMillis() / 1000.0) - (SystemClock.elapsedRealtimeNanos() / 1e9)

    fun start(surfaceProvider: Preview.SurfaceProvider? = null) {
        val cameraProviderFuture = ProcessCameraProvider.getInstance(context)
        cameraProviderFuture.addListener({
            try {
                cameraProvider = cameraProviderFuture.get()
                bindCamera(surfaceProvider)
            } catch (e: Exception) {
                // Handle camera initialization failure
            }
        }, ContextCompat.getMainExecutor(context))
    }

    private fun bindCamera(surfaceProvider: Preview.SurfaceProvider?) {
        val provider = cameraProvider ?: return
        provider.unbindAll()

        val cameraSelector = CameraSelector.DEFAULT_BACK_CAMERA

        val resolutionSelector = ResolutionSelector.Builder()
            .setResolutionStrategy(
                ResolutionStrategy(
                    Size(targetWidth, targetHeight),
                    ResolutionStrategy.FALLBACK_RULE_CLOSEST_HIGHER_THEN_LOWER
                )
            )
            .build()

        val analysis = ImageAnalysis.Builder()
            .setResolutionSelector(resolutionSelector)
            .setOutputImageFormat(ImageAnalysis.OUTPUT_IMAGE_FORMAT_RGBA_8888)
            .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
            .build()

        analysis.setAnalyzer(executor) { imageProxy ->
            try {
                processImage(imageProxy)
            } finally {
                imageProxy.close()
            }
        }
        imageAnalysis = analysis

        val useCases = mutableListOf<androidx.camera.core.UseCase>(analysis)

        if (surfaceProvider != null) {
            val preview = Preview.Builder().build()
            preview.setSurfaceProvider(surfaceProvider)
            previewUseCase = preview
            useCases.add(preview)
        }

        provider.bindToLifecycle(lifecycleOwner, cameraSelector, *useCases.toTypedArray())
    }

    fun attachPreview(surfaceProvider: Preview.SurfaceProvider) {
        val provider = cameraProvider ?: return
        val cameraSelector = CameraSelector.DEFAULT_BACK_CAMERA
        val preview = Preview.Builder().build()
        preview.setSurfaceProvider(surfaceProvider)
        previewUseCase = preview

        val analysis = imageAnalysis
        if (analysis != null) {
            provider.unbindAll()
            provider.bindToLifecycle(lifecycleOwner, cameraSelector, analysis, preview)
        }
    }

    fun detachPreview() {
        val provider = cameraProvider ?: return
        val cameraSelector = CameraSelector.DEFAULT_BACK_CAMERA
        val analysis = imageAnalysis
        previewUseCase = null
        if (analysis != null) {
            provider.unbindAll()
            provider.bindToLifecycle(lifecycleOwner, cameraSelector, analysis)
        }
    }

    private fun processImage(image: ImageProxy) {
        val timestampNs = image.imageInfo.timestamp
        if (lastFrameTimeNs > 0 && (timestampNs - lastFrameTimeNs) < minFrameIntervalNs) {
            // Software throttle to target FPS
            return
        }
        lastFrameTimeNs = timestampNs

        val planes = image.planes
        if (planes.isEmpty()) return
        val buffer: ByteBuffer = planes[0].buffer
        val pixelStride = planes[0].pixelStride
        val rowStride = planes[0].rowStride
        val width = image.width
        val height = image.height
        val rotationDegrees = image.imageInfo.rotationDegrees

        val epochS = CaptureConfig.timestampNsToEpochS(timestampNs, bootToEpochOffsetS)

        val rgbaBytes = ByteArray(width * height * 4)
        val (uprightWidth, uprightHeight) = extractUprightRgba(
            buffer = buffer,
            width = width,
            height = height,
            rowStride = rowStride,
            pixelStride = pixelStride,
            rotationDegrees = rotationDegrees,
            outBytes = rgbaBytes,
        )

        // The buffer is now upright: downstream receives rotationDegrees = 0
        frameCallback(rgbaBytes, uprightWidth, uprightHeight, 0, epochS)
    }

    fun stop() {
        cameraProvider?.unbindAll()
        cameraProvider = null
        imageAnalysis = null
        previewUseCase = null
        executor.shutdown()
    }

    companion object {
        /**
         * Extracts and rotates RGBA_8888 buffer to upright orientation into outBytes.
         * Returns Pair(uprightWidth, uprightHeight).
         */
        fun extractUprightRgba(
            buffer: ByteBuffer,
            width: Int,
            height: Int,
            rowStride: Int,
            pixelStride: Int,
            rotationDegrees: Int,
            outBytes: ByteArray,
        ): Pair<Int, Int> {
            val srcInts = buffer.duplicate().order(ByteOrder.nativeOrder()).asIntBuffer()
            val dstInts = ByteBuffer.wrap(outBytes).order(ByteOrder.nativeOrder()).asIntBuffer()
            val strideInts = rowStride / 4

            when (rotationDegrees) {
                90 -> {
                    val outW = height
                    val outH = width
                    for (yOut in 0 until outH) {
                        val origX = yOut
                        val rowOffset = yOut * outW
                        for (xOut in 0 until outW) {
                            val origY = height - 1 - xOut
                            dstInts.put(rowOffset + xOut, srcInts.get(origY * strideInts + origX))
                        }
                    }
                    return outW to outH
                }
                180 -> {
                    for (yOut in 0 until height) {
                        val origY = height - 1 - yOut
                        val rowOffset = yOut * width
                        for (xOut in 0 until width) {
                            val origX = width - 1 - xOut
                            dstInts.put(rowOffset + xOut, srcInts.get(origY * strideInts + origX))
                        }
                    }
                    return width to height
                }
                270 -> {
                    val outW = height
                    val outH = width
                    for (yOut in 0 until outH) {
                        val origX = width - 1 - yOut
                        val rowOffset = yOut * outW
                        for (xOut in 0 until outW) {
                            val origY = xOut
                            dstInts.put(rowOffset + xOut, srcInts.get(origY * strideInts + origX))
                        }
                    }
                    return outW to outH
                }
                else -> {
                    if (pixelStride == 4 && rowStride == width * 4) {
                        val dup = buffer.duplicate()
                        dup.position(0)
                        dup.get(outBytes, 0, width * height * 4)
                    } else {
                        val dup = buffer.duplicate()
                        var dstOffset = 0
                        val rowBytes = width * 4
                        for (row in 0 until height) {
                            dup.position(row * rowStride)
                            dup.get(outBytes, dstOffset, rowBytes)
                            dstOffset += rowBytes
                        }
                    }
                    return width to height
                }
            }
        }
    }
}
