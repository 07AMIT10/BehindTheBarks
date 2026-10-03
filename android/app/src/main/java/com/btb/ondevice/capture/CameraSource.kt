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

        // Extract contiguous RGBA byte array
        val rgbaBytes: ByteArray
        if (pixelStride == 4 && rowStride == width * 4) {
            rgbaBytes = ByteArray(buffer.remaining())
            buffer.get(rgbaBytes)
        } else {
            // Unpack row strides
            rgbaBytes = ByteArray(width * height * 4)
            var dstOffset = 0
            val rowBytes = width * 4
            for (row in 0 until height) {
                buffer.position(row * rowStride)
                buffer.get(rgbaBytes, dstOffset, rowBytes)
                dstOffset += rowBytes
            }
        }

        frameCallback(rgbaBytes, width, height, rotationDegrees, epochS)
    }

    fun stop() {
        cameraProvider?.unbindAll()
        cameraProvider = null
        imageAnalysis = null
        previewUseCase = null
        executor.shutdown()
    }
}
