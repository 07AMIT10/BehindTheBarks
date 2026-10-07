package com.btb.ondevice.ui

import android.Manifest
import android.app.Activity
import android.app.AlertDialog
import android.content.ClipboardManager
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.content.SharedPreferences
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.IBinder
import android.os.PowerManager
import android.provider.Settings
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.widget.Button
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.camera.view.PreviewView
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import com.btb.ondevice.capture.MonitorService
import com.btb.ondevice.capture.MonitorServiceListener
import com.btb.ondevice.net.ConnectionState
import com.btb.ondevice.util.QrDecoder

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
    private lateinit var privacyButton: Button
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
                        val isPriv = monitorService?.isPrivacyMode() ?: false
                        updatePrivacyButton(isPriv)
                        if (isPriv) {
                            ambientStatusText.text = "🛡️ Privacy Active · Camera Muted"
                            ambientStatusText.setTextColor(Color.parseColor("#E53935"))
                        }
                        val emo = overlayState.emotionLabel
                        if (!emo.isNullOrBlank()) {
                            val pct = (overlayState.confidence * 100).toInt()
                            ambientEmotionText.text = "Last state: $emo ($pct%)"
                        }
                        val deg = overlayState.degradeLevel
                        if (deg != null && deg != "NORMAL" && !isPriv) {
                            ambientStatusText.text = "● Monitoring Active (🌡️ Throttled)"
                            ambientStatusText.setTextColor(Color.parseColor("#FFB300"))
                        }
                    }
                }

                override fun onConnectionUpdate(state: ConnectionState) {
                    runOnUiThread {
                        updateConnectionUi(state)
                    }
                }

                override fun onQrCodeDetected(rawText: String) {
                    runOnUiThread {
                        val config = QrDecoder.parsePairingText(rawText)
                        if (config != null) {
                            urlEditText.setText(config.url)
                            prefs.edit().putString(KEY_URL, config.url).apply()
                            config.token?.let {
                                prefs.edit().putString(KEY_TOKEN, it).apply()
                            }
                            config.deviceName?.let {
                                deviceEditText.setText(it)
                                prefs.edit().putString(KEY_DEVICE, it).apply()
                            }
                            Toast.makeText(this@MainActivity, "✓ Paired to Dashboard!", Toast.LENGTH_LONG).show()
                        }
                    }
                }
            })
            val isPriv = monitorService?.isPrivacyMode() ?: false
            updatePrivacyButton(isPriv)
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

        // URL Quick Actions row (Scan QR & Paste)
        val urlActionsRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            setPadding(0, 4, 0, 4)
        }

        val scanQrBtn = Button(this).apply {
            text = "📷 Scan QR"
            textSize = 11f
            setBackgroundColor(Color.parseColor("#00897B"))
            setTextColor(Color.WHITE)
            setOnClickListener {
                startQrScan()
            }
        }
        urlActionsRow.addView(scanQrBtn, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply { rightMargin = 8 })

        val pasteBtn = Button(this).apply {
            text = "📋 Paste"
            textSize = 11f
            setBackgroundColor(Color.parseColor("#5E35B1"))
            setTextColor(Color.WHITE)
            setOnClickListener {
                handlePaste()
            }
        }
        urlActionsRow.addView(pasteBtn, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        controlsPanel.addView(urlActionsRow)

        // Preset buttons row
        val presetsLayout = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            setPadding(0, 4, 0, 8)
        }

        val cloudPreset = Button(this).apply {
            text = "Cloud WSS"
            textSize = 11f
            setBackgroundColor(Color.parseColor("#1565C0"))
            setTextColor(Color.WHITE)
            setOnClickListener {
                showCloudPrompt()
            }
        }
        presetsLayout.addView(cloudPreset, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1.2f).apply { rightMargin = 8 })

        val wifiPreset = Button(this).apply {
            text = "Local Wi-Fi"
            textSize = 11f
            setBackgroundColor(Color.parseColor("#37474F"))
            setTextColor(Color.WHITE)
            setOnClickListener {
                showLocalWifiPrompt()
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

        val defaultDevice = prefs.getString(KEY_DEVICE, Build.MODEL) ?: Build.MODEL
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
            text = "🔄 Flip"
            textSize = 12f
            setBackgroundColor(Color.parseColor("#37474F"))
            setTextColor(Color.WHITE)
            isEnabled = false
            setOnClickListener {
                val isBack = monitorService?.flipCamera() ?: true
                text = if (isBack) "🔄 Back" else "🔄 Front"
            }
        }
        toolsRow.addView(flipButton, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply { rightMargin = 8 })

        privacyButton = Button(this).apply {
            text = "🛡️ Privacy"
            textSize = 12f
            setBackgroundColor(Color.parseColor("#37474F"))
            setTextColor(Color.WHITE)
            isEnabled = false
            setOnClickListener {
                val on = monitorService?.togglePrivacyMode() ?: false
                updatePrivacyButton(on)
            }
        }
        toolsRow.addView(privacyButton, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
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
        handleIntent(intent)
    }

    override fun onNewIntent(intent: Intent?) {
        super.onNewIntent(intent)
        setIntent(intent)
        handleIntent(intent)
    }

    private fun handleIntent(intent: Intent?) {
        val uri = intent?.data
        if (uri != null && (uri.scheme == "wagwatch" || uri.scheme == "btb")) {
            val linkUrl = uri.getQueryParameter("url")
            val linkToken = uri.getQueryParameter("token")
            val linkDevice = uri.getQueryParameter("device")
            if (!linkUrl.isNullOrBlank()) {
                urlEditText.setText(linkUrl)
                prefs.edit().putString(KEY_URL, linkUrl).apply()
            }
            if (!linkToken.isNullOrBlank()) {
                prefs.edit().putString(KEY_TOKEN, linkToken).apply()
            }
            if (!linkDevice.isNullOrBlank()) {
                deviceEditText.setText(linkDevice)
                prefs.edit().putString(KEY_DEVICE, linkDevice).apply()
            }
            Toast.makeText(this, "✓ Paired to Server via Link!", Toast.LENGTH_SHORT).show()
        }

        val extraUrl = intent?.getStringExtra("extra_server_url") ?: intent?.getStringExtra("url")
        if (!extraUrl.isNullOrBlank()) {
            urlEditText.setText(extraUrl)
            prefs.edit().putString(KEY_URL, extraUrl).apply()
        }
        val extraToken = intent?.getStringExtra("extra_token") ?: intent?.getStringExtra("token")
        if (!extraToken.isNullOrBlank()) {
            prefs.edit().putString(KEY_TOKEN, extraToken).apply()
        }
        val extraDevice = intent?.getStringExtra("extra_device_name") ?: intent?.getStringExtra("device_name")
        if (!extraDevice.isNullOrBlank()) {
            deviceEditText.setText(extraDevice)
            prefs.edit().putString(KEY_DEVICE, extraDevice).apply()
        }

        when (intent?.action) {
            ACTION_START -> {
                if (!isMonitoring) {
                    checkPermissionsAndStart()
                }
            }
            ACTION_STOP -> {
                if (isMonitoring) {
                    stopMonitoringService()
                }
            }
            ACTION_TOGGLE_PRIVACY -> {
                if (isMonitoring) {
                    val newMode = monitorService?.togglePrivacyMode() ?: false
                    updatePrivacyButton(newMode)
                }
            }
            ACTION_TOGGLE_TORCH -> {
                if (isMonitoring) {
                    val newTorch = monitorService?.toggleTorch() ?: false
                    updateTorchButton(newTorch)
                }
            }
            ACTION_ENTER_AMBIENT -> {
                if (isMonitoring && !isAmbientMode) {
                    enterAmbientMode()
                }
            }
            ACTION_EXIT_AMBIENT -> {
                if (isAmbientMode) {
                    exitAmbientMode()
                }
            }
        }
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

    private var pendingStartAfterPermission = false

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<out String>,
        grantResults: IntArray
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == 101) {
            val hasCamera = ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED
            val hasMic = ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED

            if (hasCamera && hasMic) {
                if (pendingStartAfterPermission) {
                    pendingStartAfterPermission = false
                    startMonitoringService()
                }
            } else {
                pendingStartAfterPermission = false
                showPermissionDeniedDialog()
            }
        }
    }

    private fun showPermissionDeniedDialog() {
        AlertDialog.Builder(this)
            .setTitle("Camera & Microphone Required")
            .setMessage("Behind The Barks requires camera and microphone access to detect your dog's posture, expressions, and barking sounds.\n\nPlease grant permissions in App Settings to proceed.")
            .setPositiveButton("Open Settings") { _, _ ->
                try {
                    val intent = Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS).apply {
                        data = Uri.fromParts("package", packageName, null)
                    }
                    startActivity(intent)
                } catch (_: Exception) {}
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun checkPermissionsAndStart() {
        val hasCamera = ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED
        val hasMic = ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED

        if (!hasCamera || !hasMic) {
            pendingStartAfterPermission = true
            requestPermissionsIfNeeded()
            return
        }

        startMonitoringService()
    }

    private fun checkBatteryOptimization() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            val pm = getSystemService(Context.POWER_SERVICE) as? PowerManager
            val pkg = packageName
            if (pm != null && !pm.isIgnoringBatteryOptimizations(pkg)) {
                if (!prefs.getBoolean(KEY_BATTERY_TIP_SHOWN, false)) {
                    prefs.edit().putBoolean(KEY_BATTERY_TIP_SHOWN, true).apply()
                    AlertDialog.Builder(this)
                        .setTitle("🔋 Keep Monitoring Active")
                        .setMessage("Android will stop camera streaming after 15 minutes unless battery optimization is disabled for Behind The Barks.\n\nWould you like to disable battery optimization for this camera station?")
                        .setPositiveButton("Disable Optimization") { _, _ ->
                            try {
                                val intent = Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS).apply {
                                    data = Uri.parse("package:$pkg")
                                }
                                startActivity(intent)
                            } catch (_: Exception) {
                                try {
                                    startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS))
                                } catch (_: Exception) {}
                            }
                        }
                        .setNegativeButton("Later", null)
                        .show()
                }
            }
        }
    }

    private fun checkStationModeTip() {
        if (!prefs.getBoolean(KEY_STATION_TIP_SHOWN, false)) {
            prefs.edit().putBoolean(KEY_STATION_TIP_SHOWN, true).apply()
            AlertDialog.Builder(this)
                .setTitle("🐾 Station Setup Tip")
                .setMessage("Android disables camera hardware when the physical power button is pressed.\n\nInstead, tap '🌙 Station Dim' — it turns the screen pitch-black to save power while keeping the camera live.")
                .setPositiveButton("Got it!", null)
                .show()
        }
    }

    private fun showLocalWifiPrompt() {
        val input = EditText(this).apply {
            hint = "e.g. 192.168.1.50"
            setTextColor(Color.WHITE)
            setHintTextColor(Color.GRAY)
            val current = urlEditText.text.toString()
            val match = Regex("""ws://([0-9.]+):""").find(current)
            if (match != null) {
                setText(match.groupValues[1])
            }
        }
        AlertDialog.Builder(this)
            .setTitle("Local Wi-Fi Server")
            .setMessage("Enter the local IP address of your computer running the Behind The Barks server:")
            .setView(input)
            .setPositiveButton("Set URL") { _, _ ->
                val ip = input.text.toString().trim()
                if (ip.isNotEmpty()) {
                    urlEditText.setText("ws://$ip:8000/ingest-events")
                }
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun showCloudPrompt() {
        val input = EditText(this).apply {
            hint = "e.g. your-tunnel.trycloudflare.com"
            setTextColor(Color.WHITE)
            setHintTextColor(Color.GRAY)
            val current = urlEditText.text.toString()
            val match = Regex("""wss://([^/]+)""").find(current)
            if (match != null) {
                setText(match.groupValues[1])
            }
        }
        AlertDialog.Builder(this)
            .setTitle("Cloud WSS Server")
            .setMessage("Enter the domain or Cloudflare Tunnel of your Behind The Barks server:")
            .setView(input)
            .setPositiveButton("Set URL") { _, _ ->
                val domain = input.text.toString().trim()
                    .removePrefix("https://")
                    .removePrefix("http://")
                    .removePrefix("wss://")
                    .removePrefix("ws://")
                    .removeSuffix("/ingest-events")
                    .removeSuffix("/")
                if (domain.isNotEmpty()) {
                    urlEditText.setText("wss://$domain/ingest-events")
                }
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun handlePaste() {
        val clipboard = getSystemService(Context.CLIPBOARD_SERVICE) as? ClipboardManager
        val clip = clipboard?.primaryClip
        if (clip != null && clip.itemCount > 0) {
            val text = clip.getItemAt(0)?.text?.toString()?.trim() ?: ""
            val config = QrDecoder.parsePairingText(text)
            if (config != null) {
                urlEditText.setText(config.url)
                prefs.edit().putString(KEY_URL, config.url).apply()
                config.token?.let {
                    prefs.edit().putString(KEY_TOKEN, it).apply()
                }
                config.deviceName?.let {
                    deviceEditText.setText(it)
                    prefs.edit().putString(KEY_DEVICE, it).apply()
                }
                Toast.makeText(this, "✓ Pasted server config!", Toast.LENGTH_SHORT).show()
            } else if (text.startsWith("ws://") || text.startsWith("wss://")) {
                urlEditText.setText(text)
                prefs.edit().putString(KEY_URL, text).apply()
                Toast.makeText(this, "✓ Pasted WebSocket URL!", Toast.LENGTH_SHORT).show()
            } else {
                Toast.makeText(this, "Clipboard does not contain a valid server URL", Toast.LENGTH_LONG).show()
            }
        } else {
            Toast.makeText(this, "Clipboard is empty", Toast.LENGTH_SHORT).show()
        }
    }

    private fun startQrScan() {
        val service = monitorService
        if (service != null && isMonitoring) {
            service.setScanningQr(true)
            Toast.makeText(this, "📷 Point camera at Dashboard QR code", Toast.LENGTH_SHORT).show()
        } else {
            Toast.makeText(this, "Tap 'Start Monitoring' or 'Paste' to connect. Point camera at QR code while monitoring.", Toast.LENGTH_LONG).show()
        }
    }

    private fun startMonitoringService() {
        val url = urlEditText.text.toString().trim()
        val device = deviceEditText.text.toString().trim()
        val token = prefs.getString(KEY_TOKEN, null)

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
            if (!token.isNullOrBlank()) {
                putExtra(MonitorService.EXTRA_TOKEN, token)
            }
        }

        checkBatteryOptimization()
        checkStationModeTip()

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
        privacyButton.isEnabled = true
        updatePrivacyButton(monitorService?.isPrivacyMode() ?: false)
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
        privacyButton.isEnabled = false
        updateTorchButton(false)
        updatePrivacyButton(false)
        flipButton.text = "🔄 Flip"
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

    private fun updatePrivacyButton(on: Boolean) {
        if (on) {
            privacyButton.text = "🛡️ Privacy ON"
            privacyButton.setBackgroundColor(Color.parseColor("#D32F2F"))
            privacyButton.setTextColor(Color.WHITE)
        } else {
            privacyButton.text = "🛡️ Privacy"
            privacyButton.setBackgroundColor(Color.parseColor("#37474F"))
            privacyButton.setTextColor(Color.WHITE)
        }
    }

    override fun onStart() {
        super.onStart()
        if (isBound && isMonitoring) {
            monitorService?.attachCameraPreview(previewView.surfaceProvider)
        }
    }

    override fun onStop() {
        if (isBound && isMonitoring) {
            monitorService?.detachCameraPreview()
        }
        super.onStop()
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
        const val ACTION_START = "com.btb.ondevice.action.START"
        const val ACTION_STOP = "com.btb.ondevice.action.STOP"
        const val ACTION_TOGGLE_PRIVACY = "com.btb.ondevice.action.TOGGLE_PRIVACY"
        const val ACTION_TOGGLE_TORCH = "com.btb.ondevice.action.TOGGLE_TORCH"
        const val ACTION_ENTER_AMBIENT = "com.btb.ondevice.action.ENTER_AMBIENT"
        const val ACTION_EXIT_AMBIENT = "com.btb.ondevice.action.EXIT_AMBIENT"

        private const val PREFS_NAME = "btb_station_prefs"
        private const val KEY_URL = "server_url"
        private const val KEY_DEVICE = "device_name"
        private const val KEY_TOKEN = "access_token"
        private const val KEY_BATTERY_TIP_SHOWN = "battery_tip_shown"
        private const val KEY_STATION_TIP_SHOWN = "station_tip_shown"
        private val DEFAULT_URL = com.btb.ondevice.BuildConfig.DEFAULT_INGEST_URL
    }
}