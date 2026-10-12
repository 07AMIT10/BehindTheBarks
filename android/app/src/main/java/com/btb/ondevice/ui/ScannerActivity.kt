package com.btb.ondevice.ui

import android.Manifest
import android.app.Activity
import android.content.pm.PackageManager
import android.os.Bundle
import android.util.Log
import android.util.TypedValue
import android.view.Gravity
import android.view.ViewGroup
import android.widget.Button
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleOwner
import androidx.lifecycle.LifecycleRegistry
import com.btb.ondevice.net.PairResult
import com.btb.ondevice.net.StationKey
import com.btb.ondevice.util.PairingTarget
import com.btb.ondevice.util.QrDecoder
import kotlinx.coroutines.MainScope
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import java.util.concurrent.Executors

/**
 * Standalone QR pairing (T2.5): a spare phone pairs BEFORE monitoring ever starts, which is what a
 * first-run tester does. The camera preview is shown full screen so the tester can aim at the dashboard
 * QR, and the result (paired, expired, failed) is shown on screen instead of a short toast.
 *
 * One scan is one pairing attempt. After an attempt, scanning stays off until the tester taps
 * "Scan again", so a code the server already used is never re-sent in a loop.
 */
class ScannerActivity : Activity(), LifecycleOwner {
    private val lifecycleRegistry = LifecycleRegistry(this)
    override val lifecycle: Lifecycle get() = lifecycleRegistry
    private val scope = MainScope()
    private val executor = Executors.newSingleThreadExecutor()

    /** True while the analyzer may read a QR. Turned off by the first readable code. */
    @Volatile private var armed = true

    private lateinit var previewView: PreviewView
    private lateinit var statusText: TextView
    private lateinit var retryButton: Button

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        previewView = PreviewView(this).apply {
            scaleType = PreviewView.ScaleType.FILL_CENTER
            layoutParams = FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT)
        }
        statusText = TextView(this).apply {
            text = "Point the camera at the dashboard pairing QR"
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 18f)
            setTextColor(0xFFFFFFFF.toInt())
            gravity = Gravity.CENTER
        }
        retryButton = Button(this).apply {
            text = "Scan again"
            visibility = android.view.View.GONE
            setOnClickListener { rearm() }
        }
        val cancelButton = Button(this).apply {
            text = "Cancel"
            setOnClickListener { finish() }
        }
        val panel = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(0xCC000000.toInt())
            setPadding(dp(20), dp(16), dp(20), dp(24))
            addView(statusText)
            addView(retryButton)
            addView(cancelButton)
        }
        setContentView(FrameLayout(this).apply {
            addView(previewView)
            addView(panel, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.BOTTOM))
        })
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) {
            ActivityCompat.requestPermissions(this, arrayOf(Manifest.permission.CAMERA), 1)
            return
        }
        startCamera()
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (grantResults.firstOrNull() == PackageManager.PERMISSION_GRANTED) {
            startCamera()
        } else {
            showResult("Camera permission is needed to read the QR. Allow it in Settings, then reopen this screen.", error = true, retry = false)
        }
    }

    private fun startCamera() {
        val future = ProcessCameraProvider.getInstance(this)
        future.addListener({
            val provider = future.get()
            val preview = Preview.Builder().build().also { it.setSurfaceProvider(previewView.surfaceProvider) }
            val analysis = ImageAnalysis.Builder()
                .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                .build()
            analysis.setAnalyzer(executor) { proxy -> analyze(proxy) }
            provider.unbindAll()
            provider.bindToLifecycle(this, CameraSelector.DEFAULT_BACK_CAMERA, preview, analysis)
        }, ContextCompat.getMainExecutor(this))
    }

    private fun analyze(proxy: ImageProxy) {
        try {
            if (!armed) return
            val rgba = rgbaFrom(proxy)
            val text = rgba?.let { QrDecoder.decodeRgba(it, proxy.width, proxy.height) }
            val target = text?.let { QrDecoder.parsePairingText(it) }
            if (target is PairingTarget.Code && armed) {
                armed = false
                runOnUiThread { pair(target) }
            }
        } catch (t: Throwable) {
            Log.w("Scanner", "frame failed", t)
        } finally {
            proxy.close()
        }
    }

    /** YUV_420_888 -> the RGBA byte array QrDecoder expects (simple BT.601 conversion). */
    private fun rgbaFrom(proxy: ImageProxy): ByteArray? = try {
        val w = proxy.width
        val h = proxy.height
        val y = proxy.planes[0].buffer
        val u = proxy.planes[1].buffer
        val v = proxy.planes[2].buffer
        val yStride = proxy.planes[0].rowStride
        val uvStride = proxy.planes[1].rowStride
        val uvPixel = proxy.planes[1].pixelStride
        val out = ByteArray(w * h * 4)
        var i = 0
        for (py in 0 until h) {
            for (px in 0 until w) {
                val yv = y.get(py * yStride + px).toInt() and 0xFF
                val uvIndex = (py / 2) * uvStride + (px / 2) * uvPixel
                val uv = u.get(uvIndex).toInt() and 0xFF
                val vv = v.get(uvIndex).toInt() and 0xFF
                out[i++] = (yv + 1.402f * (vv - 128)).toInt().coerceIn(0, 255).toByte()
                out[i++] = (yv - 0.344f * (uv - 128) - 0.714f * (vv - 128)).toInt().coerceIn(0, 255).toByte()
                out[i++] = (yv + 1.772f * (uv - 128)).toInt().coerceIn(0, 255).toByte()
                out[i++] = 0xFF.toByte()
            }
        }
        out
    } catch (t: Throwable) {
        Log.w("Scanner", "conversion failed", t)
        null
    }

    private fun pair(target: PairingTarget.Code) {
        val prefs = getSharedPreferences(StationPrefs.NAME, MODE_PRIVATE)
        val deviceName = prefs.getString(StationPrefs.DEVICE, android.os.Build.MODEL) ?: android.os.Build.MODEL
        showResult("Pairing this phone…", error = false, retry = false)
        scope.launch {
            when (val result = PairingFlow.pairAndSave(prefs, target, deviceName, StationKey.getOrCreate())) {
                is PairResult.Ok -> {
                    showResult("✓ Paired. Close this screen, then tap Start Monitoring.", error = false, retry = false)
                    previewView.postDelayed({ finish() }, 2000)
                }
                PairResult.InvalidCode ->
                    showResult("This code is expired or already used. Get a new code on the dashboard, then tap Scan again.", error = true)
                is PairResult.Failed -> {
                    val message = if (result.detail == "HTTP 429") {
                        "Too many attempts. Wait one minute, then tap Scan again."
                    } else {
                        "Pairing failed (${result.detail}). Check the dashboard is open on this computer, then tap Scan again."
                    }
                    showResult(message, error = true)
                }
            }
        }
    }

    /** Scanning starts again only when the tester asks for it. */
    private fun rearm() {
        armed = true
        showResult("Point the camera at the dashboard pairing QR", error = false, retry = false)
    }

    private fun showResult(message: String, error: Boolean, retry: Boolean = error) {
        statusText.text = message
        statusText.setTextColor(if (error) 0xFFFF6B6B.toInt() else 0xFFFFFFFF.toInt())
        retryButton.visibility = if (retry) android.view.View.VISIBLE else android.view.View.GONE
    }

    private fun dp(value: Int): Int =
        TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, value.toFloat(), resources.displayMetrics).toInt()

    override fun onStart() {
        super.onStart()
        lifecycleRegistry.currentState = Lifecycle.State.STARTED
    }

    override fun onResume() {
        super.onResume()
        lifecycleRegistry.currentState = Lifecycle.State.RESUMED
    }

    override fun onPause() {
        lifecycleRegistry.currentState = Lifecycle.State.STARTED
        super.onPause()
    }

    override fun onDestroy() {
        lifecycleRegistry.currentState = Lifecycle.State.DESTROYED
        scope.cancel()
        executor.shutdown()
        super.onDestroy()
    }
}
