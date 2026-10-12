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
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import androidx.camera.view.PreviewView
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import com.btb.ondevice.capture.MonitorService
import com.btb.ondevice.capture.MonitorServiceListener
import com.btb.ondevice.net.ConnectionState
import com.btb.ondevice.net.PairResult
import com.btb.ondevice.net.StationKey
import kotlinx.coroutines.MainScope
import kotlinx.coroutines.launch
import com.btb.ondevice.util.PairingTarget
import com.btb.ondevice.util.QrDecoder

class MainActivity : Activity() {

    private lateinit var previewView: PreviewView
    private lateinit var overlayView: DebugOverlayView
    private lateinit var controlsPanel: LinearLayout
    private lateinit var controlsScroll: ScrollView
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
    private lateinit var viewersBadge: TextView
    private lateinit var disconnectViewersButton: Button
    private lateinit var ambientViewersText: TextView

    private lateinit var prefs: SharedPreferences

    private var monitorService: MonitorService? = null
    private var isBound = false
    private var isMonitoring = false
    private var householdRef: TextView? = null
    private var unpairButton: Button? = null
    private val dim = StationDimState()
    private val isAmbientMode: Boolean get() = dim.isOn
    private var uiResumed = false
    private var approvalDialog: AlertDialog? = null

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
                        monitorService?.effectiveMode()?.let { setModeButton(it) }
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
                    runOnUiThread { applyPairingPayload(rawText) }
                }

                override fun onViewersCountChanged(videoViewers: Int, audioViewers: Int) {
                    runOnUiThread {
                        updateViewersUi(videoViewers)
                    }
                }

                override fun isUiInFront(): Boolean = uiResumed

                override fun onModeApprovalRequested(requestedWire: String) {
                    runOnUiThread { showApprovalDialog() }
                }

                override fun onModeApprovalResolved() {
                    runOnUiThread { dismissApprovalDialog() }
                }

                override fun onPairingInvalid(reason: String) {
                    runOnUiThread { showRemovedFromHousehold() }
                }
            })
            val service = monitorService
            if (service != null && service.isMonitoringNow()) {
                // Reattached: a rotation or font change recreated this screen while the service kept running.
                setMonitoringUi(true)
                service.currentConnectionState()?.let { updateConnectionUi(it) }
                if (dim.onSessionConfirmed(monitoring = true)) enterAmbientMode()
            } else {
                dim.onSessionLost()
            }
            val isPriv = monitorService?.isPrivacyMode() ?: false
            updatePrivacyButton(isPriv)
            monitorService?.effectiveMode()?.let { setModeButton(it) }
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
        dim.onRecreated(savedOn = savedInstanceState?.getBoolean(KEY_DIM_ON, false) == true)

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
            setPadding(dp(32), dp(14), dp(32), dp(14))
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
            ).apply { topMargin = dp(40) }
            setOnClickListener {
                expandControls()
            }
        }
        root.addView(floatingPill)

        // 4. Controls panel (vertical layout at top)
        // Scrollable so small screens and large font scales can reach every control; the column is
        // capped at 600dp and centred on tablets. The panel background covers only its content.
        controlsPanel = CappedWidthLinearLayout(this, dp(MAX_CONTENT_WIDTH_DP)).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(16), dp(24), dp(16), dp(16))
            setBackgroundColor(Color.argb(210, 18, 18, 18))
        }
        val centredColumn = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
            addView(controlsPanel, LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
            ))
        }
        controlsScroll = ScrollView(this).apply {
            layoutParams = FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT
            )
            // Keep the first and last controls clear of the status bar and navigation bar (edge to edge).
            fitsSystemWindows = true
            isVerticalScrollBarEnabled = true
            addView(centredColumn, FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.WRAP_CONTENT
            ))
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
            setPadding(dp(16), dp(12), dp(16), dp(12))
        }
        controlsPanel.addView(urlEditText)

        // URL Quick Actions row (Scan QR & Paste)
        val urlActionsRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            setPadding(dp(0), dp(4), dp(0), dp(4))
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
        urlActionsRow.addView(scanQrBtn, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply { rightMargin = dp(8) })

        // v2 pairing works before monitoring: the scanner is its own screen (T2.5).
        val pairQrBtn = Button(this).apply {
            text = "🔗 Pair by QR"
            textSize = 11f
            setBackgroundColor(Color.parseColor("#1565C0"))
            setTextColor(Color.WHITE)
            setOnClickListener {
                // The scanner's camera start unbinds every CameraX use case in this process, which would
                // silently stop a running monitor's camera while its socket stays up (no frames after pairing).
                if (isMonitoring) {
                    Toast.makeText(this@MainActivity, "Stop Monitoring first, then pair", Toast.LENGTH_LONG).show()
                } else {
                    startActivity(android.content.Intent(this@MainActivity, ScannerActivity::class.java))
                }
            }
        }
        urlActionsRow.addView(pairQrBtn, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply { rightMargin = dp(8) })

        // The paired household, and a way out of it.
        // Stacked, not side by side: a wrapping label beside a weighted button is cut off at large font sizes.
        val householdRow = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(0), dp(8), dp(0), dp(0)) }
        val householdLabel = TextView(this).apply { textSize = 11f }
        val unpairBtn = Button(this).apply {
            text = "Unpair"
            textSize = 11f
            setBackgroundColor(Color.parseColor("#B71C1C"))
            setTextColor(Color.WHITE)
            setOnClickListener { unpairStation() }
        }
        householdRow.addView(householdLabel, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        householdRow.addView(unpairBtn, LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        householdRef = householdLabel
        unpairButton = unpairBtn

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
        controlsPanel.addView(householdRow)

        // Preset buttons row
        val presetsLayout = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            setPadding(dp(0), dp(4), dp(0), dp(8))
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
        presetsLayout.addView(cloudPreset, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1.2f).apply { rightMargin = dp(8) })

        val wifiPreset = Button(this).apply {
            text = "Local Wi-Fi"
            textSize = 11f
            setBackgroundColor(Color.parseColor("#37474F"))
            setTextColor(Color.WHITE)
            setOnClickListener {
                showLocalWifiPrompt()
            }
        }
        presetsLayout.addView(wifiPreset, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply { rightMargin = dp(8) })

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
            setPadding(dp(16), dp(12), dp(16), dp(12))
        }
        controlsPanel.addView(deviceEditText)

        statusTextView = TextView(this).apply {
            text = "● Idle · Ready"
            setTextColor(Color.LTGRAY)
            textSize = 13f
            setPadding(dp(0), dp(10), dp(0), dp(10))
        }
        controlsPanel.addView(statusTextView)

        val actionsRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            setPadding(dp(0), dp(4), dp(0), dp(0))
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
        actionsRow.addView(toggleButton, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 2f).apply { rightMargin = dp(12) })

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
            setPadding(dp(0), dp(8), dp(0), dp(0))
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
        toolsRow.addView(torchButton, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply { rightMargin = dp(8) })

        flipButton = Button(this).apply {
            text = "🔄 Flip"
            textSize = 12f
            setBackgroundColor(Color.parseColor("#37474F"))
            setTextColor(Color.WHITE)
            isEnabled = false
            setOnClickListener {
                val isBack = monitorService?.flipCamera() ?: true
                text = if (isBack) "🔄 Back" else "🔄 Front"
                contentDescription = if (isBack) "Flip camera, now back" else "Flip camera, now front"
            }
        }
        toolsRow.addView(flipButton, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply { rightMargin = dp(8) })

        // T3.2: one button that shows the current mode and opens the 4-option chooser.
        privacyButton = Button(this).apply {
            text = "🛡️ Full"
            textSize = 12f
            setBackgroundColor(Color.parseColor("#37474F"))
            setTextColor(Color.WHITE)
            isEnabled = false
            setOnClickListener { showModeChooser() }
        }
        toolsRow.addView(privacyButton, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        controlsPanel.addView(toolsRow)

        // T3.4: viewer indicator badge and disconnect action
        val viewersRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(dp(0), dp(6), dp(0), dp(0))
        }
        viewersBadge = TextView(this).apply {
            text = "👁️ 0 viewers watching"
            setTextColor(Color.parseColor("#4FC3F7"))
            textSize = 12f
            visibility = View.GONE
        }
        viewersRow.addView(viewersBadge, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))

        disconnectViewersButton = Button(this).apply {
            text = "Disconnect"
            textSize = 11f
            setBackgroundColor(Color.parseColor("#D32F2F"))
            setTextColor(Color.WHITE)
            visibility = View.GONE
            setOnClickListener {
                monitorService?.disconnectViewers()
                Toast.makeText(this@MainActivity, "Viewers disconnected", Toast.LENGTH_SHORT).show()
            }
        }
        viewersRow.addView(disconnectViewersButton, LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        controlsPanel.addView(viewersRow)

        root.addView(controlsScroll)

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
            setPadding(dp(0), dp(0), dp(0), dp(16))
        }
        ambientContent.addView(ambientTitle)

        ambientStatusText = TextView(this).apply {
            text = "● Monitoring Active (Station Mode)"
            setTextColor(Color.parseColor("#43A047"))
            textSize = 14f
            gravity = Gravity.CENTER
            setPadding(dp(0), dp(0), dp(0), dp(8))
        }
        ambientContent.addView(ambientStatusText)

        ambientEmotionText = TextView(this).apply {
            text = "Last state: Waiting for dog..."
            setTextColor(Color.parseColor("#757575"))
            textSize = 13f
            gravity = Gravity.CENTER
            setPadding(dp(0), dp(0), dp(0), dp(24))
        }
        ambientContent.addView(ambientEmotionText)

        ambientViewersText = TextView(this).apply {
            text = ""
            setTextColor(Color.parseColor("#4FC3F7"))
            textSize = 13f
            gravity = Gravity.CENTER
            setPadding(dp(0), dp(0), dp(0), dp(12))
            visibility = View.GONE
        }
        ambientContent.addView(ambientViewersText)

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
        enforceTouchTargets(controlsPanel)
        enforceTouchTargets(floatingPill)
        applyAccessibility(viewersRow)
        requestPermissionsIfNeeded()
        // A rotation or font change recreates this screen while the service keeps capturing.
        // Reattach to the running service instead of treating the screen as idle.
        if (MonitorService.isServiceRunning) {
            isBound = bindService(Intent(this, MonitorService::class.java), serviceConnection, 0)
        }
        handleIntent(intent)
    }

    private fun dp(value: Int): Int = TouchTarget.dpToPx(value, resources.displayMetrics.density)

    /** The framework's AlertDialog buttons are 36dp tall; the touch target is 48dp. */
    private fun sizeDialogButtons(dialog: AlertDialog) {
        for (which in intArrayOf(AlertDialog.BUTTON_POSITIVE, AlertDialog.BUTTON_NEGATIVE, AlertDialog.BUTTON_NEUTRAL)) {
            dialog.getButton(which)?.let { button ->
                button.minHeight = dp(MIN_TOUCH_DP)
                button.minWidth = dp(MIN_TOUCH_DP)
            }
        }
    }

    private fun AlertDialog.Builder.showSized(): AlertDialog = show().also { sizeDialogButtons(it) }

    /** Every tappable control is at least 48dp in both directions (the Material touch target). */
    private fun enforceTouchTargets(view: View) {
        if (view is Button || view is EditText || view.isClickable) {
            view.minimumHeight = maxOf(view.minimumHeight, dp(MIN_TOUCH_DP))
            view.minimumWidth = maxOf(view.minimumWidth, dp(MIN_TOUCH_DP))
        }
        if (view is ViewGroup) {
            for (i in 0 until view.childCount) enforceTouchTargets(view.getChildAt(i))
        }
    }

    /**
     * Accessibility: decorative layers are hidden from TalkBack, the status line and viewer count
     * are polite live regions, icon or emoji labels get a speakable description, and the viewer row
     * is always in the tree when it is shown. Focus follows the order the controls are added, which
     * is top to bottom as drawn.
     */
    private fun applyAccessibility(viewers: LinearLayout) {
        previewView.importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO
        overlayView.importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO
        statusTextView.accessibilityLiveRegion = View.ACCESSIBILITY_LIVE_REGION_POLITE
        viewersBadge.accessibilityLiveRegion = View.ACCESSIBILITY_LIVE_REGION_POLITE
        viewers.importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_YES
        viewersBadge.importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_YES
        disconnectViewersButton.importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_YES
        disconnectViewersButton.contentDescription = "Disconnect all viewers"
        dimButton.contentDescription = "Station Dim: black screen, camera stays on"
        collapseButton.contentDescription = "Hide the control panel"
        ambientOverlay.contentDescription = "Station Dim is on. Tap to wake the screen."
        describeEmojiLabels(controlsPanel)
    }

    /** Buttons whose text carries emoji get a plain-words description; explicit ones are kept. */
    private fun describeEmojiLabels(view: View) {
        if (view is Button && view.contentDescription == null) {
            view.contentDescription = spokenLabel(view.text.toString())
        }
        if (view is ViewGroup) {
            for (i in 0 until view.childCount) describeEmojiLabels(view.getChildAt(i))
        }
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
                val host = runCatching { android.net.Uri.parse(linkUrl).host }.getOrNull() ?: linkUrl
                // A deep link is attacker-controllable (any web page can fire one): confirm before it
                // touches the stored configuration, and never take a token for a different host.
                // The host check reads the stored URL BEFORE the link overwrites it.
                AlertDialog.Builder(this)
                    .setTitle("Connect this station to $host?")
                    .setMessage("A link asked to point WagWatch at $host${
                        if (!linkToken.isNullOrBlank() && !DeepLinkPolicy.mayApplyToken(prefs.getString(KEY_URL, ""), linkUrl))
                            "\n\nIts access token was ignored because it does not match your current server." else ""
                    }")
                    .setPositiveButton("Connect") { _, _ ->
                        val applyToken = !linkToken.isNullOrBlank() &&
                            DeepLinkPolicy.mayApplyToken(prefs.getString(KEY_URL, ""), linkUrl)
                        urlEditText.setText(linkUrl)
                        prefs.edit().putString(KEY_URL, linkUrl).apply()
                        if (!linkDevice.isNullOrBlank()) {
                            deviceEditText.setText(linkDevice)
                            prefs.edit().putString(KEY_DEVICE, linkDevice).apply()
                        }
                        if (applyToken) prefs.edit().putString(KEY_TOKEN, linkToken).apply()
                    }
                    .setNegativeButton("Ignore", null)
                    .showSized()
            }
        }

        if (!com.btb.ondevice.BuildConfig.DEBUG) return  // extras are the adb dev loop: release builds ignore them
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

        // A pairing payload from the adb dev loop goes through the same path as a scanned QR (see applyPairingPayload).
        // Removed from the intent so a recreation of this screen does not pair the one-time code twice.
        val extraPair = intent?.getStringExtra(EXTRA_PAIR_PAYLOAD)
        if (!extraPair.isNullOrBlank()) {
            intent?.removeExtra(EXTRA_PAIR_PAYLOAD)
            applyPairingPayload(extraPair)
        }

        when (intent?.action) {
            ACTION_START -> {
                if (!isMonitoring && !MonitorService.isServiceRunning) {
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
        if (!dim.turnOn(monitoring = isMonitoring)) return
        ambientOverlay.visibility = View.VISIBLE
        controlsScroll.visibility = View.GONE
        floatingPill.visibility = View.GONE
        previewView.visibility = View.INVISIBLE // Pause viewfinder rendering to save GPU/display power

        // Dim screen brightness to minimum
        val lp = window.attributes
        lp.screenBrightness = 0.01f
        window.attributes = lp
    }

    private fun exitAmbientMode() {
        dim.turnOff()
        ambientOverlay.visibility = View.GONE
        previewView.visibility = View.VISIBLE

        // Restore normal brightness
        val lp = window.attributes
        lp.screenBrightness = WindowManager.LayoutParams.BRIGHTNESS_OVERRIDE_NONE
        window.attributes = lp

        controlsScroll.visibility = View.VISIBLE
    }

    private fun collapseControls() {
        controlsScroll.visibility = View.GONE
        floatingPill.visibility = View.VISIBLE
    }

    private fun expandControls() {
        floatingPill.visibility = View.GONE
        controlsScroll.visibility = View.VISIBLE
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
            .showSized()
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
                        .showSized()
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
                .showSized()
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
            .showSized()
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
            .showSized()
    }

    private fun handlePaste() {
        val clipboard = getSystemService(Context.CLIPBOARD_SERVICE) as? ClipboardManager
        val clip = clipboard?.primaryClip
        if (clip != null && clip.itemCount > 0) {
            val text = clip.getItemAt(0)?.text?.toString()?.trim() ?: ""
            if (applyPairingPayload(text) == null) {
                Toast.makeText(this, "Clipboard is not a pairing payload", Toast.LENGTH_SHORT).show()
            }
        } else {
            Toast.makeText(this, "Clipboard is empty", Toast.LENGTH_SHORT).show()
        }
    }

    /**
     * The one path for a pairing payload, whatever delivered it: a dashboard QR scan from the monitor, the
     * clipboard paste, or the debug intent extra `extra_pair_payload`. A v2 code pairs the station; a v1
     * payload fills in the server settings. Returns the parsed target, or null when the text is not a payload.
     */
    private fun applyPairingPayload(rawText: String): PairingTarget? {
        val target = QrDecoder.parsePairingText(rawText)
        when (target) {
            is PairingTarget.Code -> pairStation(target)  // v2: signed identity (T2.5)
            is PairingTarget.Manual -> {
                val config = target.config
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
            }
            null -> Unit
        }
        return target
    }

    /**
     * v2 pairing (T2.5): trade the one-time code for a station id. The Keystore key signs the
     * server's later challenges, so nothing secret leaves the device here.
     */
    private fun pairStation(target: PairingTarget.Code) {
        Toast.makeText(this, "Pairing station…", Toast.LENGTH_SHORT).show()
        MainScope().launch {
            val deviceName = prefs.getString(KEY_DEVICE, Build.MODEL) ?: Build.MODEL
            when (val result = PairingFlow.pairAndSave(prefs, target, deviceName, StationKey.getOrCreate())) {
                is PairResult.Ok -> {
                    urlEditText.setText(target.wsUrl)
                    refreshHouseholdLabel()
                    Toast.makeText(this@MainActivity, "✓ Station paired — start monitoring", Toast.LENGTH_LONG).show()
                }
                PairResult.InvalidCode -> Toast.makeText(this@MainActivity, "That pairing code is expired or used", Toast.LENGTH_LONG).show()
                is PairResult.Failed -> Toast.makeText(this@MainActivity, "Pairing failed: ${result.detail}", Toast.LENGTH_LONG).show()
            }
        }
    }

    /**
     * The owner removed this station (close 4410) or the server refused its identity (4401). The server will
     * not take it back, so the station forgets its pairing, stops monitoring, and says so. Start stays usable.
     */
    private fun showRemovedFromHousehold() {
        prefs.edit()
            .remove(StationPrefs.STATION_ID)
            .remove(StationPrefs.HOUSEHOLD_ID)
            .remove(StationPrefs.PAIR_URL)
            .apply()
        refreshHouseholdLabel()
        if (isMonitoring) stopMonitoringService()
        setMonitoringUi(false)
        statusTextView.text = REMOVED_MESSAGE
        statusTextView.setTextColor(Color.parseColor("#FF8A80"))
    }

    private fun refreshHouseholdLabel() {
        householdRef?.text = prefs.getString(StationPrefs.HOUSEHOLD_ID, null)?.let { "Household: $it" }
            ?: "No household paired (use Pair by QR)"
    }

    /** Start and Unpair only apply to a paired station; Pair by QR stays available either way. */
    private fun refreshPairingUi() {
        val paired = !prefs.getString(KEY_STATION_ID, null).isNullOrBlank()
        unpairButton?.visibility = if (paired) View.VISIBLE else View.GONE
        toggleButton.visibility = if (paired) View.VISIBLE else View.GONE
        refreshHouseholdLabel()
    }

    /**
     * Drop the station identity and stop monitoring, since a running monitor would keep streaming under the
     * old identity. The next start pairs again from the dashboard (Pair by QR).
     */
    private fun unpairStation() {
        if (isMonitoring) stopMonitoringService()
        prefs.edit()
            .remove(StationPrefs.STATION_ID)
            .remove(StationPrefs.HOUSEHOLD_ID)
            .remove(StationPrefs.PAIR_URL)
            .apply()
        refreshPairingUi()
        statusTextView.text = "Unpaired. Tap Pair by QR to pair this phone again."
        statusTextView.setTextColor(Color.parseColor("#FFB74D"))
        Toast.makeText(this, "Station unpaired", Toast.LENGTH_SHORT).show()
    }

    private fun startQrScan() {
        val service = monitorService
        when (ScanRoute.choose(monitoring = service != null && isMonitoring)) {
            ScanRoute.Target.MONITOR_SERVICE -> {
                service?.setScanningQr(true)
                Toast.makeText(this, "📷 Point camera at Dashboard QR code", Toast.LENGTH_SHORT).show()
            }
            // Pairing does not need monitoring: ScannerActivity pairs and saves the station itself.
            ScanRoute.Target.STANDALONE_SCANNER ->
                startActivity(android.content.Intent(this, ScannerActivity::class.java))
        }
    }

    private fun startMonitoringService() {
        val url = urlEditText.text.toString().trim()
        val device = deviceEditText.text.toString().trim()
        // A paired station (households, T2.5) identifies itself with its id and Keystore key, not the shared token.
        val stationId = prefs.getString(KEY_STATION_ID, null)?.takeIf { it.isNotBlank() }
        val token = if (stationId != null) null else prefs.getString(KEY_TOKEN, null)

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
            if (stationId != null) {
                putExtra(MonitorService.EXTRA_STATION_ID, stationId)
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
        setMonitoringUi(true)
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

        setMonitoringUi(false)
        statusTextView.text = "● Idle · Ready"
        overlayView.updateState(OverlayState())
        updateViewersUi(0)
    }

    /** The controls that depend on monitoring. Used on start, on stop and when a recreated screen reattaches. */
    private fun setMonitoringUi(on: Boolean) {
        isMonitoring = on
        toggleButton.text = if (on) "Stop Monitoring" else "Start Monitoring"
        toggleButton.setBackgroundColor(Color.parseColor(if (on) "#FF1744" else "#00E676"))
        dimButton.isEnabled = on
        torchButton.isEnabled = on
        flipButton.isEnabled = on
        privacyButton.isEnabled = on
        if (on) {
            updatePrivacyButton(monitorService?.isPrivacyMode() ?: false)
            monitorService?.effectiveMode()?.let { setModeButton(it) }
        } else {
            updateTorchButton(false)
            updatePrivacyButton(false)
            flipButton.text = "🔄 Flip"
            describe(flipButton)
        }
    }

    /** Speakable text for a control whose label changes (emoji and status symbols are dropped). */
    private fun describe(view: TextView) {
        view.contentDescription = spokenLabel(view.text.toString())
    }

    private fun updateViewersUi(count: Int) {
        if (count > 0) {
            val label = if (count == 1) "👁️ 1 viewer watching" else "👁️ $count viewers watching"
            viewersBadge.text = label
            viewersBadge.visibility = View.VISIBLE
            disconnectViewersButton.visibility = View.VISIBLE
            ambientViewersText.text = label
            ambientViewersText.visibility = View.VISIBLE
        } else {
            viewersBadge.visibility = View.GONE
            disconnectViewersButton.visibility = View.GONE
            ambientViewersText.visibility = View.GONE
        }
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
        torchButton.contentDescription = if (on) "Light, on" else "Light, off"
    }

    /** T3.2: the four viewing modes, with one line each. This control is the ONLY place the cap can
     *  be raised: the dashboard can lower it, but raising always needs the person holding the phone. */
    private fun showModeChooser() {
        val modes = com.btb.ondevice.capture.ViewMode.entries.toTypedArray()
        val labels = arrayOf(
            "⛔ Blackout\nNothing leaves the phone",
            "🔇 Privacy\nBarks and behaviour only",
            "🖼️ Limited\nBlurred snapshots",
            "👁️ Full\nLive video",
        )
        val current = monitorService?.effectiveMode() ?: com.btb.ondevice.capture.ViewMode.FULL
        // Each row is a touch target of at least 48dp (the framework rows are only about 40dp at large fonts).
        val rows = object : ArrayAdapter<String>(this, android.R.layout.simple_list_item_single_choice, labels) {
            override fun getView(position: Int, convertView: View?, parent: ViewGroup): View =
                super.getView(position, convertView, parent).also { it.minimumHeight = dp(MIN_TOUCH_DP) }
        }
        android.app.AlertDialog.Builder(this)
            .setTitle("What may be shared?")
            .setSingleChoiceItems(rows, modes.indexOf(current)) { dialog, which ->
                monitorService?.setStationCapFromDevice(modes[which])
                updatePrivacyButton(modes[which] == com.btb.ondevice.capture.ViewMode.BLACKOUT)
                setModeButton(modes[which])
                dialog.dismiss()
            }
            .setNegativeButton("Cancel", null)
            .showSized()
    }

    private fun modeButtonLabel(mode: com.btb.ondevice.capture.ViewMode): String = when (mode) {
        com.btb.ondevice.capture.ViewMode.BLACKOUT -> "⛔ Blackout"
        com.btb.ondevice.capture.ViewMode.PRIVACY -> "🔇 Privacy"
        com.btb.ondevice.capture.ViewMode.LIMITED -> "🖼️ Limited"
        com.btb.ondevice.capture.ViewMode.FULL -> "👁️ Full"
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
        describe(privacyButton)
    }

    /** The sharing-mode button. Its label names the mode, and TalkBack hears the same words. */
    private fun setModeButton(mode: com.btb.ondevice.capture.ViewMode) {
        privacyButton.text = modeButtonLabel(mode)
        privacyButton.contentDescription = "Sharing: ${spokenLabel(modeButtonLabel(mode))}. Tap to change."
    }

    override fun onResume() {
        super.onResume()
        uiResumed = true
        refreshPairingUi()  // a pairing done in ScannerActivity is shown when we come back
        // A raise that arrived while we were away is still waiting for an answer: show it now.
        if (monitorService?.pendingApprovalMode() != null) showApprovalDialog()
    }

    override fun onPause() {
        uiResumed = false
        // Leaving the screen: the prompt moves to the notification so it is still answerable.
        if (approvalDialog?.isShowing == true) {
            dismissApprovalDialog()
            monitorService?.repostApprovalNotification()
        }
        super.onPause()
    }

    /** T3.2: the on-device prompt for a remote raise. Allow = 15 minutes; Keep private = no change. */
    private fun showApprovalDialog() {
        val mode = monitorService?.pendingApprovalMode() ?: return
        if (approvalDialog?.isShowing == true) return
        approvalDialog = AlertDialog.Builder(this)
            .setTitle("Sharing request")
            .setMessage(com.btb.ondevice.capture.approvalQuestion(mode))
            .setPositiveButton("Allow") { _, _ -> monitorService?.allowPendingRaise() }
            .setNegativeButton("Keep private") { _, _ -> monitorService?.keepPendingPrivate() }
            .setCancelable(false)
            .create()
            .also {
                it.show()
                sizeDialogButtons(it)
            }
    }

    private fun dismissApprovalDialog() {
        approvalDialog?.dismiss()
        approvalDialog = null
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

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        outState.putBoolean(KEY_DIM_ON, dim.isOn)
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
        /** Debug-only (BuildConfig.DEBUG): a v2 pairing payload delivered by adb, identical to a scanned QR. */
        const val EXTRA_PAIR_PAYLOAD = "extra_pair_payload"
        const val KEY_DIM_ON = "station_dim_on"
        const val REMOVED_MESSAGE = "Removed from household · pair again with Pair by QR"
        const val ACTION_STOP = "com.btb.ondevice.action.STOP"
        const val ACTION_TOGGLE_PRIVACY = "com.btb.ondevice.action.TOGGLE_PRIVACY"
        const val ACTION_TOGGLE_TORCH = "com.btb.ondevice.action.TOGGLE_TORCH"
        const val ACTION_ENTER_AMBIENT = "com.btb.ondevice.action.ENTER_AMBIENT"
        const val ACTION_EXIT_AMBIENT = "com.btb.ondevice.action.EXIT_AMBIENT"

        private const val MAX_CONTENT_WIDTH_DP = 600
        private const val MIN_TOUCH_DP = TouchTarget.MIN_DP

        private const val PREFS_NAME = StationPrefs.NAME
        private const val KEY_URL = StationPrefs.URL
        private const val KEY_DEVICE = StationPrefs.DEVICE
        private const val KEY_TOKEN = StationPrefs.TOKEN
        private const val KEY_STATION_ID = StationPrefs.STATION_ID   // v2 pairing (T2.5)
        private const val KEY_HOUSEHOLD_ID = StationPrefs.HOUSEHOLD_ID
        private const val KEY_PAIR_URL = StationPrefs.PAIR_URL
        private const val KEY_BATTERY_TIP_SHOWN = "battery_tip_shown"
        private const val KEY_STATION_TIP_SHOWN = "station_tip_shown"
        private val DEFAULT_URL = com.btb.ondevice.BuildConfig.DEFAULT_INGEST_URL
    }
}