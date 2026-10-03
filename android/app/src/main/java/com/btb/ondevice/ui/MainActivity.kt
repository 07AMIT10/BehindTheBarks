package com.btb.ondevice.ui

import android.Manifest
import android.app.Activity
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.content.SharedPreferences
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.os.Build
import android.os.Bundle
import android.os.IBinder
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
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
    private lateinit var controlsPanel: LinearLayout
    private lateinit var floatingPill: TextView
    private lateinit var ambientOverlay: FrameLayout
    private lateinit var ambientStatusText: TextView
    private lateinit var ambientEmotionText: TextView

    private lateinit var urlEditText: EditText
    private lateinit var deviceEditText: EditText
    private lateinit var statusTextView: TextView
    private lateinit var toggleButton: Button
    private lateinit var dimButton: Button
    private lateinit var torchButton: Button
    private lateinit var flipButton: Button
    private lateinit var collapseButton: Button

    private lateinit var prefs: SharedPreferences

    private var monitorService: MonitorService? = null
    private var isBound = false
    private var isMonitoring = false
    private var isAmbientMode = false

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
                        val emo = overlayState.emotionLabel
                        if (!emo.isNullOrBlank()) {
                            val pct = (overlayState.confidence * 100).toInt()
                            ambientEmotionText.text = "Last state: $emo ($pct%)"
                        }
                    }
                }

                override fun onConnectionUpdate(state: ConnectionState) {
                    runOnUiThread {
                        updateConnectionUi(state)
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
        actionBar?.hide()
        prefs = getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

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

        // 2. Overlay View (transparent, draws detection boxes & keypoints)
        overlayView = DebugOverlayView(this).apply {
            layoutParams = FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT)
        }
        root.addView(overlayView)

        // 3. Floating Pill for collapsed/kiosk mode (top-center)
        floatingPill = TextView(this).apply {
            text = "🐾 Behind The Barks · Tap to expand"
            setTextColor(Color.WHITE)
            textSize = 12f
            setPadding(32, 14, 32, 14)
            val bg = GradientDrawable().apply {
                setColor(Color.argb(200, 20, 20, 20))
                cornerRadius = 32f
            }
            background = bg
            visibility = View.GONE
            layoutParams = FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.WRAP_CONTENT,
                FrameLayout.LayoutParams.WRAP_CONTENT,
                Gravity.TOP or Gravity.CENTER_HORIZONTAL
            ).apply { topMargin = 40 }
            setOnClickListener {
                expandControls()
            }
        }
        root.addView(floatingPill)

        // 4. Controls panel (vertical layout at top)
        controlsPanel = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(32, 48, 32, 24)
            setBackgroundColor(Color.argb(210, 18, 18, 18))
            layoutParams = FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.WRAP_CONTENT,
                Gravity.TOP
            )
        }

        val headerRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }

        val title = TextView(this).apply {
            text = "🐾 Behind The Barks"
            setTextColor(Color.WHITE)
            textSize = 17f
        }
        headerRow.addView(title, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))

        collapseButton = Button(this).apply {
            text = "▲ Hide HUD"
            textSize = 11f
            setTextColor(Color.LTGRAY)
            setBackgroundColor(Color.TRANSPARENT)
            setOnClickListener {
                collapseControls()
            }
        }
        headerRow.addView(collapseButton)
        controlsPanel.addView(headerRow)

        val defaultUrl = prefs.getString(KEY_URL, DEFAULT_URL) ?: DEFAULT_URL
        urlEditText = EditText(this).apply {
            hint = "Server WebSocket URL"
            setText(defaultUrl)
            setTextColor(Color.WHITE)
            setHintTextColor(Color.GRAY)
            setBackgroundColor(Color.argb(140, 50, 50, 50))
            setPadding(16, 12, 16, 12)
        }
        controlsPanel.addView(urlEditText)

        // Preset buttons row
        val presetsLayout = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            setPadding(0, 8, 0, 8)
        }

        val cloudPreset = Button(this).apply {
            text = "Cloud WSS"
            textSize = 11f
            setBackgroundColor(Color.parseColor("#1565C0"))
            setTextColor(Color.WHITE)
            setOnClickListener {
                urlEditText.setText(CLOUD_URL)
            }
        }
        presetsLayout.addView(cloudPreset, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1.2f).apply { rightMargin = 8 })

        val wifiPreset = Button(this).apply {
            text = "Local Wi-Fi"
            textSize = 11f
            setBackgroundColor(Color.parseColor("#37474F"))
            setTextColor(Color.WHITE)
            setOnClickListener {
                urlEditText.setText("ws://192.168.1.74:8000/ingest-events")
            }
        }
        presetsLayout.addView(wifiPreset, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply { rightMargin = 8 })

        val usbPreset = Button(this).apply {
            text = "USB (Local)"
            textSize = 11f
            setBackgroundColor(Color.parseColor("#37474F"))
            setTextColor(Color.WHITE)
            setOnClickListener {
                urlEditText.setText("ws://127.0.0.1:8000/ingest-events")
            }
        }
        presetsLayout.addView(usbPreset, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        controlsPanel.addView(presetsLayout)

        val defaultDevice = prefs.getString(KEY_DEVICE, "Galaxy-A07") ?: "Galaxy-A07"
        deviceEditText = EditText(this).apply {
            hint = "Device Name"
            setText(defaultDevice)
            setTextColor(Color.WHITE)
            setHintTextColor(Color.GRAY)
            setBackgroundColor(Color.argb(140, 50, 50, 50))
            setPadding(16, 12, 16, 12)
        }
        controlsPanel.addView(deviceEditText)

        statusTextView = TextView(this).apply {
            text = "● Idle · Ready"
            setTextColor(Color.LTGRAY)
            textSize = 13f
            setPadding(0, 10, 0, 10)
        }
        controlsPanel.addView(statusTextView)

        val actionsRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            setPadding(0, 4, 0, 0)
        }

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
        actionsRow.addView(toggleButton, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 2f).apply { rightMargin = 12 })

        dimButton = Button(this).apply {
            text = "🌙 Station Dim"
            setBackgroundColor(Color.parseColor("#455A64"))
            setTextColor(Color.WHITE)
            isEnabled = false
            setOnClickListener {
                enterAmbientMode()
            }
        }
        actionsRow.addView(dimButton, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1.4f))
        controlsPanel.addView(actionsRow)

        val toolsRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            setPadding(0, 8, 0, 0)
        }

        torchButton = Button(this).apply {
            text = "🔦 Light"
            textSize = 12f
            setBackgroundColor(Color.parseColor("#37474F"))
            setTextColor(Color.WHITE)
            isEnabled = false
            setOnClickListener {
                val on = monitorService?.toggleTorch() ?: false
                updateTorchButton(on)
            }
        }
        toolsRow.addView(torchButton, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply { rightMargin = 8 })

        flipButton = Button(this).apply {
            text = "🔄 Flip Lens"
            textSize = 12f
            setBackgroundColor(Color.parseColor("#37474F"))
            setTextColor(Color.WHITE)
            isEnabled = false
            setOnClickListener {
                val isBack = monitorService?.flipCamera() ?: true
                text = if (isBack) "🔄 Flip (Back)" else "🔄 Flip (Front)"
            }
        }
        toolsRow.addView(flipButton, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        controlsPanel.addView(toolsRow)

        root.addView(controlsPanel)

        // 5. Ambient / Station Mode Fullscreen Overlay (pitch black to save power & prevent OLED burn-in)
        ambientOverlay = FrameLayout(this).apply {
            layoutParams = FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT)
            setBackgroundColor(Color.parseColor("#080808"))
            visibility = View.GONE
            isClickable = true
            isFocusable = true
            setOnClickListener {
                exitAmbientMode()
            }
        }

        val ambientContent = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            layoutParams = FrameLayout.LayoutParams(FrameLayout.LayoutParams.WRAP_CONTENT, FrameLayout.LayoutParams.WRAP_CONTENT, Gravity.CENTER)
        }

        val ambientTitle = TextView(this).apply {
            text = "🐾 Behind The Barks"
            setTextColor(Color.parseColor("#616161"))
            textSize = 20f
            gravity = Gravity.CENTER
            setPadding(0, 0, 0, 16)
        }
        ambientContent.addView(ambientTitle)

        ambientStatusText = TextView(this).apply {
            text = "● Monitoring Active (Station Mode)"
            setTextColor(Color.parseColor("#43A047"))
            textSize = 14f
            gravity = Gravity.CENTER
            setPadding(0, 0, 0, 8)
        }
        ambientContent.addView(ambientStatusText)

        ambientEmotionText = TextView(this).apply {
            text = "Last state: Waiting for dog..."
            setTextColor(Color.parseColor("#757575"))
            textSize = 13f
            gravity = Gravity.CENTER
            setPadding(0, 0, 0, 24)
        }
        ambientContent.addView(ambientEmotionText)

        val ambientHint = TextView(this).apply {
            text = "Display dimmed to reduce power & thermals\nTap anywhere to wake"
            setTextColor(Color.parseColor("#424242"))
            textSize = 11f
            gravity = Gravity.CENTER
            setLineSpacing(4f, 1.1f)
        }
        ambientContent.addView(ambientHint)

        ambientOverlay.addView(ambientContent)
        root.addView(ambientOverlay)

        setContentView(root)
        requestPermissionsIfNeeded()
    }

    private fun enterAmbientMode() {
        if (!isMonitoring) return
        isAmbientMode = true
        ambientOverlay.visibility = View.VISIBLE
        controlsPanel.visibility = View.GONE
        floatingPill.visibility = View.GONE
        previewView.visibility = View.INVISIBLE // Pause viewfinder rendering to save GPU/display power

        // Dim screen brightness to minimum
        val lp = window.attributes
        lp.screenBrightness = 0.01f
        window.attributes = lp
    }

    private fun exitAmbientMode() {
        isAmbientMode = false
        ambientOverlay.visibility = View.GONE
        previewView.visibility = View.VISIBLE

        // Restore normal brightness
        val lp = window.attributes
        lp.screenBrightness = WindowManager.LayoutParams.BRIGHTNESS_OVERRIDE_NONE
        window.attributes = lp

        controlsPanel.visibility = View.VISIBLE
    }

    private fun collapseControls() {
        controlsPanel.visibility = View.GONE
        floatingPill.visibility = View.VISIBLE
    }

    private fun expandControls() {
        floatingPill.visibility = View.GONE
        controlsPanel.visibility = View.VISIBLE
    }

    private fun updateConnectionUi(state: ConnectionState) {
        val spooled = monitorService?.getSpooledBytes() ?: 0L
        val spooledKb = spooled / 1024

        when (state) {
            ConnectionState.CONNECTED -> {
                statusTextView.text = "● Live · Connected to Server"
                statusTextView.setTextColor(Color.parseColor("#00E676"))
                floatingPill.text = "● Live [Galaxy-A07] · Tap for Menu"
                ambientStatusText.text = "● Monitoring Active · Live"
                ambientStatusText.setTextColor(Color.parseColor("#43A047"))
            }
            ConnectionState.CONNECTING -> {
                statusTextView.text = "○ Connecting to server..."
                statusTextView.setTextColor(Color.parseColor("#FFEE58"))
                floatingPill.text = "○ Connecting... · Tap for Menu"
                ambientStatusText.text = "○ Connecting to server..."
                ambientStatusText.setTextColor(Color.parseColor("#FDD835"))
            }
            ConnectionState.DISCONNECTED -> {
                val spoolMsg = if (spooledKb > 0) " (Buffered: ${spooledKb} KB)" else ""
                statusTextView.text = "⚠ Offline · Spooling locally$spoolMsg"
                statusTextView.setTextColor(Color.parseColor("#FFB300"))
                floatingPill.text = "⚠ Offline · Spooling · Tap for Menu"
                ambientStatusText.text = "⚠ Offline · Spooling locally"
                ambientStatusText.setTextColor(Color.parseColor("#FB8C00"))
            }
            ConnectionState.TERMINATED -> {
                statusTextView.text = "● Stopped"
                statusTextView.setTextColor(Color.parseColor("#B0BEC5"))
            }
        }
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

        // Persist user inputs in preferences
        prefs.edit()
            .putString(KEY_URL, url)
            .putString(KEY_DEVICE, device)
            .apply()

        // Keep screen awake while monitoring station is running
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

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
        dimButton.isEnabled = true
        torchButton.isEnabled = true
        flipButton.isEnabled = true
        statusTextView.text = "○ Connecting to $url..."
    }

    private fun stopMonitoringService() {
        // Clear keep-screen-on flag
        window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

        if (isAmbientMode) {
            exitAmbientMode()
        }

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
        dimButton.isEnabled = false
        torchButton.isEnabled = false
        flipButton.isEnabled = false
        updateTorchButton(false)
        flipButton.text = "🔄 Flip Lens"
        statusTextView.text = "● Idle · Ready"
        overlayView.updateState(OverlayState())
    }

    private fun updateTorchButton(on: Boolean) {
        if (on) {
            torchButton.text = "💡 Light ON"
            torchButton.setBackgroundColor(Color.parseColor("#FBC02D"))
            torchButton.setTextColor(Color.BLACK)
        } else {
            torchButton.text = "🔦 Light"
            torchButton.setBackgroundColor(Color.parseColor("#37474F"))
            torchButton.setTextColor(Color.WHITE)
        }
    }

    override fun onDestroy() {
        window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        if (isBound) {
            monitorService?.detachCameraPreview()
            unbindService(serviceConnection)
            isBound = false
        }
        super.onDestroy()
    }

    companion object {
        private const val PREFS_NAME = "btb_station_prefs"
        private const val KEY_URL = "server_url"
        private const val KEY_DEVICE = "device_name"
        private const val CLOUD_URL = "wss://annually-moment-most-racial.trycloudflare.com/ingest-events"
        private const val DEFAULT_URL = CLOUD_URL
    }
}