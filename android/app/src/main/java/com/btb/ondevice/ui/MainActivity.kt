package com.btb.ondevice.ui

import android.Manifest
import android.app.Activity
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.content.pm.PackageManager
import android.graphics.Color
import android.os.Build
import android.os.Bundle
import android.os.IBinder
import android.view.Gravity
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import androidx.camera.view.PreviewView
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import com.btb.ondevice.capture.MonitorService
import com.btb.ondevice.capture.MonitorServiceListener
import com.btb.ondevice.net.ConnectionState

class MainActivity : Activity() {

    private lateinit var previewView: PreviewView
    private lateinit var overlayView: DebugOverlayView
    private lateinit var urlEditText: EditText
    private lateinit var deviceEditText: EditText
    private lateinit var statusTextView: TextView
    private lateinit var toggleButton: Button

    private var monitorService: MonitorService? = null
    private var isBound = false
    private var isMonitoring = false

    private val serviceConnection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName?, service: IBinder?) {
            val binder = service as? MonitorService.LocalBinder
            monitorService = binder?.service
            isBound = true

            // Attach Camera viewfinder to PreviewView
            monitorService?.attachCameraPreview(previewView.surfaceProvider)

            monitorService?.setListener(object : MonitorServiceListener {
                override fun onStateUpdate(overlayState: OverlayState) {
                    runOnUiThread {
                        overlayView.updateState(overlayState)
                    }
                }

                override fun onConnectionUpdate(state: ConnectionState) {
                    runOnUiThread {
                        statusTextView.text = "Connection: ${state.name}"
                        when (state) {
                            ConnectionState.CONNECTED -> statusTextView.setTextColor(Color.GREEN)
                            ConnectionState.CONNECTING -> statusTextView.setTextColor(Color.YELLOW)
                            else -> statusTextView.setTextColor(Color.parseColor("#FF5252"))
                        }
                    }
                }
            })
        }

        override fun onServiceDisconnected(name: ComponentName?) {
            monitorService = null
            isBound = false
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // Root container: FrameLayout with Camera Preview on bottom, Overlay in middle, controls on top
        val root = FrameLayout(this).apply {
            layoutParams = ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT)
            setBackgroundColor(Color.BLACK)
        }

        // 1. Camera Viewfinder (fills screen)
        previewView = PreviewView(this).apply {
            layoutParams = FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT)
            scaleType = PreviewView.ScaleType.FIT_CENTER
        }
        root.addView(previewView)

        // 2. Overlay View (fills screen, transparent background)
        overlayView = DebugOverlayView(this).apply {
            layoutParams = FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT)
        }
        root.addView(overlayView)

        // 3. Controls layout (vertical panel at top)
        val controls = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(32, 48, 32, 24)
            setBackgroundColor(Color.argb(170, 20, 20, 20))
            layoutParams = FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.WRAP_CONTENT,
                Gravity.TOP
            )
        }

        val title = TextView(this).apply {
            text = "Behind The Barks - On-Device Perception"
            setTextColor(Color.WHITE)
            textSize = 17f
            setPadding(0, 0, 0, 12)
        }
        controls.addView(title)

        urlEditText = EditText(this).apply {
            hint = "Server WebSocket URL"
            setText("ws://127.0.0.1:8000/ingest-events")
            setTextColor(Color.WHITE)
            setHintTextColor(Color.GRAY)
            setBackgroundColor(Color.argb(120, 60, 60, 60))
            setPadding(16, 12, 16, 12)
        }
        controls.addView(urlEditText)

        // Preset buttons row for quick 1-tap configuration
        val presetsLayout = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            setPadding(0, 8, 0, 8)
        }

        val usbPreset = Button(this).apply {
            text = "USB (127.0.0.1)"
            textSize = 11f
            setBackgroundColor(Color.parseColor("#37474F"))
            setTextColor(Color.WHITE)
            setOnClickListener {
                urlEditText.setText("ws://127.0.0.1:8000/ingest-events")
            }
        }
        presetsLayout.addView(usbPreset, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply { rightMargin = 8 })

        val wifiPreset = Button(this).apply {
            text = "Wi-Fi (192.168.1.74)"
            textSize = 11f
            setBackgroundColor(Color.parseColor("#37474F"))
            setTextColor(Color.WHITE)
            setOnClickListener {
                urlEditText.setText("ws://192.168.1.74:8000/ingest-events")
            }
        }
        presetsLayout.addView(wifiPreset, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply { rightMargin = 8 })

        val cloudPreset = Button(this).apply {
            text = "Cloud WSS"
            textSize = 11f
            setBackgroundColor(Color.parseColor("#1565C0"))
            setTextColor(Color.WHITE)
            setOnClickListener {
                urlEditText.setText("wss://annually-moment-most-racial.trycloudflare.com/ingest-events")
            }
        }
        presetsLayout.addView(cloudPreset, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        controls.addView(presetsLayout)

        deviceEditText = EditText(this).apply {
            hint = "Device Name"
            setText("Galaxy-A07")
            setTextColor(Color.WHITE)
            setHintTextColor(Color.GRAY)
            setBackgroundColor(Color.argb(120, 60, 60, 60))
            setPadding(16, 12, 16, 12)
        }
        controls.addView(deviceEditText)

        statusTextView = TextView(this).apply {
            text = "Status: Idle"
            setTextColor(Color.LTGRAY)
            textSize = 13f
            setPadding(0, 8, 0, 8)
        }
        controls.addView(statusTextView)

        toggleButton = Button(this).apply {
            text = "Start Monitoring"
            setBackgroundColor(Color.parseColor("#00E676"))
            setTextColor(Color.BLACK)
            setOnClickListener {
                if (isMonitoring) {
                    stopMonitoringService()
                } else {
                    checkPermissionsAndStart()
                }
            }
        }
        controls.addView(toggleButton)

        root.addView(controls)
        setContentView(root)

        // Request initial permissions
        requestPermissionsIfNeeded()
    }

    private fun requestPermissionsIfNeeded() {
        val permissions = mutableListOf(
            Manifest.permission.CAMERA,
            Manifest.permission.RECORD_AUDIO
        )
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            permissions.add(Manifest.permission.POST_NOTIFICATIONS)
        }

        val needed = permissions.filter {
            ContextCompat.checkSelfPermission(this, it) != PackageManager.PERMISSION_GRANTED
        }

        if (needed.isNotEmpty()) {
            ActivityCompat.requestPermissions(this, needed.toTypedArray(), 101)
        }
    }

    private fun checkPermissionsAndStart() {
        val hasCamera = ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED
        val hasMic = ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED

        if (!hasCamera || !hasMic) {
            requestPermissionsIfNeeded()
            return
        }

        startMonitoringService()
    }

    private fun startMonitoringService() {
        val url = urlEditText.text.toString().trim()
        val device = deviceEditText.text.toString().trim()

        val intent = Intent(this, MonitorService::class.java).apply {
            putExtra(MonitorService.EXTRA_SERVER_URL, url)
            putExtra(MonitorService.EXTRA_DEVICE_NAME, device)
        }

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            startForegroundService(intent)
        } else {
            startService(intent)
        }

        bindService(intent, serviceConnection, Context.BIND_AUTO_CREATE)
        isMonitoring = true
        toggleButton.text = "Stop Monitoring"
        toggleButton.setBackgroundColor(Color.parseColor("#FF1744"))
        statusTextView.text = "Status: Connecting to $url..."
    }

    private fun stopMonitoringService() {
        if (isBound) {
            monitorService?.detachCameraPreview()
            monitorService?.setListener(null)
            unbindService(serviceConnection)
            isBound = false
        }

        val intent = Intent(this, MonitorService::class.java).apply {
            action = MonitorService.ACTION_STOP
        }
        startService(intent)

        isMonitoring = false
        toggleButton.text = "Start Monitoring"
        toggleButton.setBackgroundColor(Color.parseColor("#00E676"))
        statusTextView.text = "Status: Stopped"
        overlayView.updateState(OverlayState())
    }

    override fun onDestroy() {
        if (isBound) {
            monitorService?.detachCameraPreview()
            unbindService(serviceConnection)
            isBound = false
        }
        super.onDestroy()
    }
}