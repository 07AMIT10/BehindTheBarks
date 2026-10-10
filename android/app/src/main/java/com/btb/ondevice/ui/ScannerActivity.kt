package com.btb.ondevice.ui

import android.Manifest
import android.content.pm.PackageManager
import android.os.Bundle
import android.util.Log
import android.util.TypedValue
import android.view.Gravity
import android.view.ViewGroup
import android.widget.FrameLayout
import android.widget.TextView
import android.widget.Toast
import android.app.Activity
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleOwner
import androidx.lifecycle.LifecycleRegistry
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import com.btb.ondevice.net.PairResult
import com.btb.ondevice.net.StationKey
import com.btb.ondevice.net.StationPairing
import com.btb.ondevice.util.PairingTarget
import com.btb.ondevice.util.QrDecoder
import kotlinx.coroutines.MainScope
import kotlinx.coroutines.launch
import java.util.concurrent.Executors

/**
 * Standalone QR pairing (T2.5): a spare phone pairs BEFORE monitoring ever starts, which is what a
 * first-run tester does. Reads one v2 code, pairs, closes itself; the monitoring session is untouched.
 * No preview is bound: the analyzer alone is enough to read a QR, and it keeps the camera use brief.
 */
class ScannerActivity : Activity(), LifecycleOwner {
    private val lifecycleRegistry = LifecycleRegistry(this)
    override val lifecycle: Lifecycle get() = lifecycleRegistry
    private val scope = MainScope()
    private val executor = Executors.newSingleThreadExecutor()
    private var done = false
    private lateinit var hint: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        hint = TextView(this).apply {
            text = "Point the camera at the dashboard pairing QR"
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 18f)
            gravity = Gravity.CENTER
            layoutParams = FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT)
        }
        setContentView(FrameLayout(this).apply { addView(hint) })
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) {
            ActivityCompat.requestPermissions(this, arrayOf(Manifest.permission.CAMERA), 1)
            return
        }
        startCamera()
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (grantResults.firstOrNull() == PackageManager.PERMISSION_GRANTED) startCamera() else finish()
    }

    private fun startCamera() {
        val future = ProcessCameraProvider.getInstance(this)
        future.addListener({
            val provider = future.get()
            val analysis = ImageAnalysis.Builder()
                .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                .build()
            analysis.setAnalyzer(executor) { proxy -> analyze(proxy) }
            provider.unbindAll()
            provider.bindToLifecycle(this, CameraSelector.DEFAULT_BACK_CAMERA, analysis as androidx.camera.core.UseCase)
        }, ContextCompat.getMainExecutor(this))
    }

    private fun analyze(proxy: ImageProxy) {
        try {
            if (done) return
            val rgba = rgbaFrom(proxy)
            val text = rgba?.let { QrDecoder.decodeRgba(it, proxy.width, proxy.height) }
            val target = text?.let { QrDecoder.parsePairingText(it) }
            if (target is PairingTarget.Code && !done) {
                done = true
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
        Toast.makeText(this, "Pairing station…", Toast.LENGTH_SHORT).show()
        scope.launch {
            val result = StationPairing.pair(target.pairUrl, target.code, deviceName, StationKey.getOrCreate())
            when (result) {
                is PairResult.Ok -> {
                    prefs.edit()
                        .putString(StationPrefs.STATION_ID, result.pairing.stationId)
                        .putString(StationPrefs.HOUSEHOLD_ID, result.pairing.householdId)
                        .putString(StationPrefs.URL, target.wsUrl)
                        .apply()
                    Toast.makeText(this@ScannerActivity, "✓ Station paired — start monitoring", Toast.LENGTH_LONG).show()
                    finish()
                }
                PairResult.InvalidCode -> {
                    done = false
                    Toast.makeText(this@ScannerActivity, "That code is expired or used", Toast.LENGTH_LONG).show()
                }
                is PairResult.Failed -> {
                    done = false
                    Toast.makeText(this@ScannerActivity, "Pairing failed: ${result.detail}", Toast.LENGTH_LONG).show()
                }
            }
        }
    }

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
        executor.shutdown()
        super.onDestroy()
    }
}
