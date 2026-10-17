package com.btb.ondevice.capture

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.graphics.Bitmap
import android.graphics.Matrix
import android.os.Binder
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import android.util.Log
import androidx.camera.core.Preview
import androidx.core.app.NotificationCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleOwner
import androidx.lifecycle.LifecycleRegistry
import com.btb.ondevice.audio.AdpcmCodec
import com.btb.ondevice.audio.AudioPipeline
import com.btb.ondevice.audio.AudioSnippetBuffer
import com.btb.ondevice.audio.YamnetClassifier
import com.btb.ondevice.config.DataConfig
import com.btb.ondevice.contracts.AudioEvent
import com.btb.ondevice.contracts.FrameEvent
import com.btb.ondevice.contracts.RulesLabel
import com.btb.ondevice.ml.LiteRtRunner
import com.btb.ondevice.ml.ModelLoader
import com.btb.ondevice.ml.ModelRegistry
import com.btb.ondevice.ml.ModelRunner
import com.btb.ondevice.net.ConnectionState
import com.btb.ondevice.net.EventSpool
import com.btb.ondevice.net.EventUploader
import com.btb.ondevice.net.EventUploaderListener
import com.btb.ondevice.net.StationKey
import com.btb.ondevice.net.UrlCheck
import com.btb.ondevice.net.UrlPolicy
import com.btb.ondevice.BuildConfig
import com.btb.ondevice.pipeline.AndroidMemoryProvider
import com.btb.ondevice.pipeline.AndroidThermalProvider
import com.btb.ondevice.pipeline.InferenceExecutor
import com.btb.ondevice.pipeline.MemoryGuard
import com.btb.ondevice.pipeline.OnDevicePipeline
import com.btb.ondevice.pipeline.ThermalGovernor
import com.btb.ondevice.ui.ListenerIndicator
import com.btb.ondevice.ui.OverlayState
import com.btb.ondevice.ui.StationPrefs
import com.btb.ondevice.vision.DogDetector
import com.btb.ondevice.vision.FaceLandmarker
import com.btb.ondevice.vision.PoseEstimator
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import java.io.ByteArrayOutputStream
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.channels.FileChannel
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicLong
import kotlin.math.roundToInt

interface MonitorServiceListener {
    fun onStateUpdate(overlayState: OverlayState) {}
    fun onConnectionUpdate(state: ConnectionState) {}
    fun onQrCodeDetected(rawText: String) {}
    /** T3.2: the effective mode changed (device or server driven). */
    fun onModeStateChanged(effectiveWire: String) {}
    /** T3.2: the owner asked for more exposure than the phone allows. Shown as a dialog when the UI is in front. */
    fun onModeApprovalRequested(requestedWire: String) {}
    /** The pending raise was answered (on the phone or in the notification) or timed out: dismiss the prompt. */
    fun onModeApprovalResolved() {}

    /** T2.4/T2.5: the server removed this station (4410) or refused its identity (4401): pair again. */
    fun onPairingInvalid(reason: String) {}
    /** True when the activity is resumed and can show the prompt itself; otherwise a notification is posted. */
    fun isUiInFront(): Boolean = false
    /** T3.4: active remote viewer count changed. */
    fun onViewersCountChanged(videoViewers: Int, audioViewers: Int) {}
    /** Flashlight / torch state changed (remote or local). */
    fun onTorchStateChanged(enabled: Boolean) {}
}

class MonitorService : Service(), LifecycleOwner {

    inner class LocalBinder : Binder() {
        val service: MonitorService get() = this@MonitorService
    }

    private val binder = LocalBinder()
    private val lifecycleRegistry = LifecycleRegistry(this)
    override val lifecycle: Lifecycle get() = lifecycleRegistry

    private var wakeLock: PowerManager.WakeLock? = null
    private var cameraSource: CameraSource? = null
    private var micSource: MicSource? = null
    private var pipeline: OnDevicePipeline? = null
    private var executor: InferenceExecutor? = null
    private var modelRegistry: ModelRegistry? = null
    private var uploader: EventUploader? = null
    private var thermalGovernor: ThermalGovernor? = null
    private var memoryGuard: MemoryGuard? = null

    private val isRunning = AtomicBoolean(false)
    /** UI overlay updates are capped (see OverlayThrottle): the camera runs far faster than a screen can use. */
    private val uiThrottle = com.btb.ondevice.ui.OverlayThrottle()
    @Volatile private var lastConnectionState: ConnectionState? = null
    private var listener: MonitorServiceListener? = null
    // T3.2: the station's own cap plus the server policy decide what may leave the phone. The
    // controller is the single place that rule lives (and it is unit-tested without Android).
    // `lazy` because it reads the persisted cap, which needs a Context.
    private val modeController: ModeController by lazy {
        ModeController(
            stationCap = ViewMode.fromWire(readStationCap()) ?: ViewMode.FULL,
            allowOwnerRaiseWithoutPrompt = true,
        )
    }
    private var previewEncoder: PreviewEncoder? = null
    private var previewLadder = PreviewLadder()
    private var ladderJob: Job? = null
    private var telemetrySampler: TelemetrySampler? = null
    private val isScanningQr = AtomicBoolean(false)
    private var networkCallback: android.net.ConnectivityManager.NetworkCallback? = null
    private var currentVideoViewers: Int = 0
    private var currentAudioViewers: Int = 0
    /** T3.9: sequence of audio frames in this monitoring session. Only advanced for frames actually built. */
    private val audioSeq = AtomicLong(0L)
    /** Reused PCM16 view of one chunk for the ADPCM encoder (only the microphone thread touches it). */
    private val audioShorts = ShortArray(AdpcmCodec.CHUNK_SAMPLES)
    /** 5s rolling PCM16 ring buffer for bioacoustic snippets on bark / whimper events. */
    private val audioSnippetBuffer = AudioSnippetBuffer(sampleRate = 16_000, durationSeconds = 5.0)
    private var voiceAudioTrack: android.media.AudioTrack? = null
    private var lastVoiceChunkMs: Long = 0L
    private var currentStatusText: String = "Monitoring room with camera & audio..."
    private val approvalHandler = android.os.Handler(android.os.Looper.getMainLooper())
    private val approvalTimeout = Runnable { resolveRaise(RaiseResult.TIMEOUT) }
    /** An approved raise lapses after 15 minutes: the phone then reports its own cap again. */
    private val raiseExpiry = Runnable {
        publishModeState(ModeReason.EXPIRED_RAISE.wire)
        listener?.onModeStateChanged(modeController.effectiveMode().wire)
    }

    /** T3.4: station requests to disconnect all active remote viewers immediately. */
    fun disconnectViewers(): Boolean = uploader?.sendKickViewers() ?: false

    fun setScanningQr(enabled: Boolean) {
        isScanningQr.set(enabled)
    }

    fun isScanningQr(): Boolean = isScanningQr.get()

    /** True while this service is capturing; an activity recreated by a rotation reattaches to it. */
    fun isMonitoringNow(): Boolean = isRunning.get()

    /** The last connection state, so a recreated activity can show it without waiting for a change. */
    fun currentConnectionState(): ConnectionState? = lastConnectionState

    fun setPrivacyMode(enabled: Boolean): Boolean {
        if (enabled) modeController.setStationCapFromDevice(ViewMode.BLACKOUT)
        else modeController.setStationCapFromDevice(ViewMode.FULL)
        publishModeState("station")
        if (enabled) {
            cameraSource?.setTorch(false)
            listener?.onStateUpdate(
                OverlayState(
                    bbox = null,
                    keypoints = null,
                    wagRoi = null,
                    emotionLabel = "PRIVACY",
                    confidence = 1.0f,
                    degradeLevel = null
                )
            )
        }
        return enabled
    }

    fun isPrivacyMode(): Boolean = modeController.effectiveMode() == ViewMode.BLACKOUT
    fun togglePrivacyMode(): Boolean = setPrivacyMode(!isPrivacyMode())

    private fun stationPrefs() =
        getSharedPreferences("btb_station_prefs", android.content.Context.MODE_PRIVATE)

    private fun readStationCap(): String? =
        stationPrefs().getString(KEY_STATION_CAP, null)

    private fun persistStationCap(mode: ViewMode) {
        stationPrefs().edit().putString(KEY_STATION_CAP, mode.wire).apply()
    }

    /** T3.2: the device UI chooses a mode outright (the 4-segment control). */
    fun setStationCapFromDevice(mode: ViewMode) {
        approvalHandler.removeCallbacks(raiseExpiry)
        modeController.setStationCapFromDevice(mode)
        persistStationCap(mode)
        if (mode == ViewMode.BLACKOUT) cameraSource?.setTorch(false)
        publishModeState("station")
        listener?.onModeStateChanged(modeController.effectiveMode().wire)
    }

    fun effectiveMode(): ViewMode = modeController.effectiveMode()

    /** T3.1: tell the server what we allow and what we are doing, after any change. */
    private fun publishModeState(reason: String) {
        uploader?.sendModeState(
            modeController.reportedCap().wire,
            modeController.effectiveMode().wire,
            reason,
        )
        previewEncoder?.enabled = modeController.shouldEncodePreview()
    }

    /** T5.2: the encoder runs at the server policy, lowered by the ladder's rung (never raised). */
    private fun applyPreviewConfig() {
        val p = modeController.policy
        previewEncoder?.config = previewLadder.effective(
            PreviewConfig(fps = p.previewFps.toDouble(), maxEdge = p.previewMaxEdge, quality = p.previewQuality)
        )
    }

    /** T3.1: a server `policy` downlink. It can lower exposure, never raise the cap. */
    private fun applyPolicy(
        mode: String, previewFps: Int, previewMaxEdge: Int, previewQuality: Int,
        audio: Boolean, videoViewers: Int, audioViewers: Int,
    ) {
        val parsed = ViewMode.fromWire(mode)
        if (parsed == null) {
            android.util.Log.w("MonitorService", "ignoring unknown policy mode=$mode")
            return
        }
        val prevVideoViewers = modeController.policy.videoViewers
        val prevAudioViewers = modeController.policy.audioViewers
        val policy = Policy(
            mode = parsed,
            previewFps = previewFps,
            previewMaxEdge = previewMaxEdge,
            previewQuality = previewQuality,
            audio = audio,
            videoViewers = videoViewers,
            audioViewers = audioViewers,
        )
        modeController.onPolicy(policy)
        applyPreviewConfig()
        previewEncoder?.enabled = modeController.shouldEncodePreview()
        uploader?.setPreviewMinInterval(policy.previewIntervalMs())
        publishModeState("policy")
        currentVideoViewers = videoViewers
        currentAudioViewers = audioViewers
        if (prevVideoViewers == 0 && videoViewers > 0) {
            playViewerChime()
        }
        if (ListenerIndicator.chimesOn(prevAudioViewers, audioViewers)) {
            playListenerChime()
        }
        updateNotification()
        listener?.onViewersCountChanged(videoViewers, audioViewers)
    }

    /** T3.2: the owner asked for more exposure. Lowering applies now; raising waits for the device. */
    private fun applyModeRequest(mode: String, from: String) {
        val requested = ViewMode.fromWire(mode)
        if (requested == null) return
        when (val outcome = modeController.onModeRequest(requested, from == "owner")) {
            is ModeController.RequestOutcome.Applied -> publishModeState(outcome.reason.wire)
            is ModeController.RequestOutcome.NeedsApproval -> {
                approvalHandler.removeCallbacks(approvalTimeout)
                approvalHandler.postDelayed(approvalTimeout, modeController.requestTimeoutMs)
                if (listener?.isUiInFront() == true) {
                    listener?.onModeApprovalRequested(requested.wire)
                } else {
                    postApprovalNotification(requested)
                }
            }
        }
    }

    /** The raise the owner asked for and nobody has answered yet, or null. */
    fun pendingApprovalMode(): ViewMode? = modeController.pendingRaise()?.mode

    /** The on-device answer: Allow for 15 minutes (the dialog button or the notification action). */
    fun allowPendingRaise(): Boolean = resolveRaise(RaiseResult.ALLOWED)

    /** The on-device answer: Keep private. */
    fun keepPendingPrivate(): Boolean = resolveRaise(RaiseResult.DENIED)

    /** Re-post the notification, e.g. when the activity goes away while its dialog is still open. */
    fun repostApprovalNotification() {
        val mode = pendingApprovalMode() ?: return
        postApprovalNotification(mode)
    }

    /**
     * Ends the pending raise (allowed, denied, or timed out), tells the server the outcome as a
     * `mode_result`, then clears both prompts (dialog and notification). Returns false when nothing was
     * pending (for example the other path already answered it).
     */
    private fun resolveRaise(answer: RaiseResult): Boolean {
        approvalHandler.removeCallbacks(approvalTimeout)
        val resolved = modeController.resolvePendingRaise(answer)
        if (resolved != null) {
            uploader?.sendModeResult(resolved.mode.wire, resolved.result.wire)
        }
        if (resolved?.result == RaiseResult.ALLOWED) {
            publishModeState(ModeReason.APPROVED_RAISE.wire)
            listener?.onModeStateChanged(modeController.effectiveMode().wire)
            approvalHandler.removeCallbacks(raiseExpiry)
            approvalHandler.postDelayed(raiseExpiry, modeController.raiseDurationMs)
        }
        cancelApprovalNotification()
        listener?.onModeApprovalResolved()
        return resolved != null
    }

    private fun postApprovalNotification(mode: ViewMode) {
        val nm = getSystemService(NotificationManager::class.java) ?: return
        val allowIntent = PendingIntent.getService(
            this, 2,
            Intent(this, MonitorService::class.java).apply { action = ACTION_ALLOW_RAISE },
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val keepIntent = PendingIntent.getService(
            this, 3,
            Intent(this, MonitorService::class.java).apply { action = ACTION_KEEP_PRIVATE },
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val openIntent = packageManager.getLaunchIntentForPackage(packageName)?.let {
            PendingIntent.getActivity(this, 4, it, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        }
        val notification = NotificationCompat.Builder(this, APPROVAL_CHANNEL_ID)
            .setContentTitle("Sharing request")
            .setContentText(approvalQuestion(mode))
            .setStyle(NotificationCompat.BigTextStyle().bigText(approvalQuestion(mode)))
            .setSmallIcon(android.R.drawable.ic_dialog_alert)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setCategory(NotificationCompat.CATEGORY_STATUS)
            .setAutoCancel(false)
            .setOngoing(true)
            .addAction(android.R.drawable.ic_menu_send, "Allow", allowIntent)
            .addAction(android.R.drawable.ic_menu_close_clear_cancel, "Keep private", keepIntent)
            .apply { openIntent?.let { setContentIntent(it) } }
            .build()
        nm.notify(APPROVAL_NOTIFICATION_ID, notification)
    }

    private fun cancelApprovalNotification() {
        getSystemService(NotificationManager::class.java)?.cancel(APPROVAL_NOTIFICATION_ID)
    }

    fun setListener(l: MonitorServiceListener?) {
        listener = l
    }

    override fun onCreate() {
        super.onCreate()
        lifecycleRegistry.currentState = Lifecycle.State.CREATED
        createNotificationChannel()
    }

    override fun onBind(intent: Intent?): IBinder = binder

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val action = intent?.action
        if (action == ACTION_STOP) {
            stopMonitoring()
            stopSelf()
            return START_NOT_STICKY
        }
        if (action == ACTION_KICK_VIEWERS) {
            disconnectViewers()
            return START_NOT_STICKY
        }
        if (action == ACTION_ALLOW_RAISE) {
            allowPendingRaise()
            return START_NOT_STICKY
        }
        if (action == ACTION_KEEP_PRIVATE) {
            keepPendingPrivate()
            return START_NOT_STICKY
        }

        val prefs = getSharedPreferences("btb_station_prefs", Context.MODE_PRIVATE)
        val defaultUrl = prefs.getString("server_url", DEFAULT_SERVER_URL) ?: DEFAULT_SERVER_URL
        val defaultDevice = prefs.getString("device_name", Build.MODEL) ?: Build.MODEL
        val defaultToken = prefs.getString("access_token", "")?.takeIf { it.isNotBlank() }

        val serverUrl = intent?.getStringExtra(EXTRA_SERVER_URL)
            ?: intent?.getStringExtra("server_url")
            ?: defaultUrl
        // H2, defence in depth: the UI refuses these links too. A refused start still calls startForeground
        // (the system requires it for a foreground start), shows the reason in the notification and stops.
        val linkCheck = UrlPolicy.check(serverUrl, BuildConfig.DEBUG)
        if (linkCheck is UrlCheck.Rejected && !isRunning.get()) {
            startForeground(NOTIFICATION_ID, buildNotification(linkCheck.reason))
            stopSelf()
            return START_NOT_STICKY
        }
        val deviceName = intent?.getStringExtra(EXTRA_DEVICE_NAME)
            ?: intent?.getStringExtra("device_name")
            ?: defaultDevice
        val token = intent?.getStringExtra(EXTRA_TOKEN)
            ?: intent?.getStringExtra("token")
            ?: defaultToken
        // A paired station (households, T2.5) identifies itself with its id and Keystore key, never a token.
        // C1: the service returns START_STICKY, so after the process dies the restart arrives with a
        // null intent. Every other start input already falls back to the pairing prefs; the station id
        // must too, or the hello loses its identity, the server answers 4401, onPairingInvalid fires
        // and the UI deletes the pairing: monitoring stops untended after a mere OS process kill.
        val stationId = StationIdentity.resolveStationId(
            intent?.getStringExtra(EXTRA_STATION_ID),
            prefs.getString(StationPrefs.STATION_ID, null),
        )

        startMonitoring(serverUrl, deviceName, if (stationId != null) null else token, stationId)
        return START_STICKY
    }

    private fun startMonitoring(serverUrl: String, deviceName: String, token: String? = null, stationId: String? = null) {
        if (!isRunning.compareAndSet(false, true)) return
        isServiceRunning = true
        modeController.beginSession()
        audioSeq.set(0L)
        cancelApprovalNotification()

        // 1. Foreground Notification
        val notification = buildNotification("Monitoring room with camera & audio...")
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            var type = ServiceInfo.FOREGROUND_SERVICE_TYPE_CAMERA
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                type = type or ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE
            }
            startForeground(NOTIFICATION_ID, notification, type)
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }

        lifecycleRegistry.currentState = Lifecycle.State.STARTED
        lifecycleRegistry.currentState = Lifecycle.State.RESUMED

        // 2. Partial WakeLock
        val pm = getSystemService(Context.POWER_SERVICE) as? PowerManager
        wakeLock = pm?.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "btb:monitor_wakelock")?.apply {
            acquire(24 * 60 * 60 * 1000L) // 24h safety limit
        }

        // 3. Load config and Model Registry
        val config = try {
            assets.open("config_data.json").bufferedReader().use {
                DataConfig.fromJson(it.readText())
            }
        } catch (_: Exception) {
            DataConfig()
        }

        val registry = initModelRegistry()
        modelRegistry = registry

        // Wire the bundled models into the perception stages. Every stage degrades
        // gracefully without its runner (detector/pose/face stay off, YAMNet falls back
        // to the energy gate), so one unloadable model must never abort service start.
        val pipe = OnDevicePipeline(
            config = config,
            dogDetector = DogDetector(runner = loadRunnerQuietly(registry, "detector")),
            poseEstimator = PoseEstimator(runner = loadRunnerQuietly(registry, "pose")),
            faceLandmarker = FaceLandmarker(runner = loadRunnerQuietly(registry, "face")),
            audioPipeline = AudioPipeline(
                classifier = YamnetClassifier(runner = loadRunnerQuietly(registry, "audio")),
            ),
        )
        pipeline = pipe

        // 4. Initialize Thermal Governor & Memory Guard
        val thermal = ThermalGovernor(AndroidThermalProvider(this))
        thermalGovernor = thermal
        val memGuard = MemoryGuard(AndroidMemoryProvider(this), registry)
        memoryGuard = memGuard

        // 5. Initialize EventUploader
        val spoolDir = File(filesDir, "spool")
        val spool = EventSpool(spoolDir)

        val up = EventUploader(
            serverUrl = serverUrl,
            deviceName = deviceName,
            token = token,
            stationId = stationId,
            signer = stationId?.let { StationKey.getOrCreate() },
            spool = spool,
            listener = object : EventUploaderListener {
                override fun onStateChanged(state: ConnectionState) {
                    lastConnectionState = state
                    listener?.onConnectionUpdate(state)
                }
                override fun onPairingInvalid(reason: String) {
                    listener?.onPairingInvalid(reason)
                }
                override fun onHelloAck() {
                    // The server forgets the cap of a previous connection: tell it on every handshake.
                    publishModeState("connect")
                }
                override fun onTreatDownlink(ts: Double) {
                    pipe.pushTreat(ts)
                    playTreatChime()
                }
                override fun onTorchDownlink(enabled: Boolean) {
                    cameraSource?.setTorch(enabled)
                    val on = cameraSource?.isTorchOn() ?: enabled
                    android.os.Handler(android.os.Looper.getMainLooper()).post {
                        listener?.onTorchStateChanged(on)
                    }
                }
                override fun onCueDownlink(sound: String, light: String?) {
                    playCue(sound, light)
                }
                override fun onVoiceDownlink(pcmBytes: ByteArray) {
                    playVoiceChunk(pcmBytes)
                }
                override fun onFlipDownlink() {
                    cameraSource?.flipCamera()
                }
                override fun onPrivacyDownlink(enabled: Boolean) {
                    // Once a `policy` was seen this session the legacy downlink is ignored completely:
                    // a remote blackout must not lower the station's own cap (T3.2, protocol doc).
                    if (modeController.ignoresLegacyPrivacy()) return
                    // T3.2: a remote privacy:false must never raise exposure. Only the on-device
                    // button (setPrivacyMode) may restore the cap.
                    val effective = modeController.onLegacyPrivacy(enabled)
                    publishModeState("station")
                    if (enabled) {
                        listener?.onModeStateChanged(effective.wire)
                    }
                }
                override fun onPolicyDownlink(
                    mode: String, previewFps: Int, previewMaxEdge: Int, previewQuality: Int,
                    audio: Boolean, videoViewers: Int, audioViewers: Int,
                ) = applyPolicy(mode, previewFps, previewMaxEdge, previewQuality, audio, videoViewers, audioViewers)
                override fun onModeRequest(mode: String, from: String) = applyModeRequest(mode, from)
            }
        )
        uploader = up
        up.start()

        // 6. Initialize PreviewEncoder (T5.2) decoupled from inference
        val enc = PreviewEncoder(
            onEncoded = { jpeg -> up.sendPreview(jpeg) }
        ).apply {
            this.config = PreviewConfig(
                fps = modeController.policy.previewFps.toDouble(),
                maxEdge = modeController.policy.previewMaxEdge,
                quality = modeController.policy.previewQuality
            )
            this.enabled = modeController.shouldEncodePreview()
        }
        previewEncoder = enc
        applyPreviewConfig()
        up.setPreviewMinInterval(modeController.policy.previewIntervalMs())

        // T5.2 step 4: once a second, feed the ladder whether thermal got worse, the current thermal
        // level (the ladder never steps up while it is above 0) and how many previews the uploader
        // dropped under backpressure since the last second.
        previewLadder = PreviewLadder()  // each monitoring session starts at rung 0
        ladderJob = CoroutineScope(Dispatchers.Default).launch {
            var prevLevel = thermal.currentLevel
            var prevOffered = up.previewsOffered.get()
            var prevDropped = up.previewDroppedBackpressure.get()
            while (true) {
                delay(1_000L)
                val level = thermal.currentLevel
                val offered = up.previewsOffered.get()
                val dropped = up.previewDroppedBackpressure.get()
                previewLadder.onSample(
                    thermalDegraded = level > prevLevel,
                    thermalLevel = level.ordinal, // DegradeLevel: 0 = L0_NORMAL, 3 = L3_CRITICAL
                    offered = (offered - prevOffered).toInt(),
                    droppedBackpressure = (dropped - prevDropped).toInt(),
                )
                prevLevel = level
                prevOffered = offered
                prevDropped = dropped
                applyPreviewConfig()
            }
        }

        // 7. Initialize TelemetrySampler (T6.1)
        val sampler = TelemetrySampler(
            uploader = { uploader },
            batteryProvider = AndroidBatteryProvider(this),
            thermalProvider = AndroidThermalProvider(this),
            memoryProvider = AndroidMemoryProvider(this),
            metricsTracker = { pipeline?.metricsTracker },
            degradeLevel = { pipeline?.scheduler?.getLevel() ?: com.btb.ondevice.pipeline.DegradeLevel.L0_NORMAL },
            previewEncoder = { previewEncoder },
            previewRung = { previewLadder.currentRung },
            audioStreaming = { isAudioStreaming() },
            sampleIntervalMs = 10_000L,
            scope = CoroutineScope(Dispatchers.Default),
        )
        telemetrySampler = sampler
        sampler.start()

        // 8. Initialize InferenceExecutor
        val exec = InferenceExecutor()
        executor = exec

        // 9. Initialize CameraSource & MicSource
        val camera = CameraSource(
            context = this,
            lifecycleOwner = this,
            targetWidth = 640,
            targetHeight = 480,
            targetFps = 15,
            frameCallback = { rgba, width, height, rotation, ts ->
                // T3.2 blackout: return before any processing. The phone no longer uploads a slate
                // -- the server renders it -- so nothing about the room leaves the device at all.
                if (modeController.effectiveMode() == ViewMode.BLACKOUT) {
                    return@CameraSource
                }

                if (isScanningQr.get()) {
                    val qrText = com.btb.ondevice.util.QrDecoder.decodeRgba(rgba, width, height)
                    if (qrText != null) {
                        isScanningQr.set(false)
                        listener?.onQrCodeDetected(qrText)
                    }
                }

                // T3.2 & T5.2: preview encoding is completely decoupled from inference.
                // We offer the raw frame to PreviewEncoder BEFORE submitting to InferenceExecutor.
                // It drops stale frames internally, enforces rate limits, downscales, and encodes
                // on a dedicated low-priority thread, so inference never waits for JPEG encoding.
                if (modeController.shouldEncodePreview()) {
                    previewEncoder?.offer(rgba, width, height, rotation)
                }

                // Check thermal governor and memory guard
                val deg = thermal.poll()
                pipe.scheduler.setLevel(deg)
                memGuard.checkMemory()

                val buffer = ByteBuffer.wrap(rgba)
                exec.submitLatest {
                    try {
                        val (frameEvent, rulesLabel) = pipe.processFrame(ts, width, height, buffer)
                        up.sendFrame(frameEvent)
                        if (rulesLabel != null) {
                            up.sendRules(rulesLabel)
                        }
                        updateUiOverlay(frameEvent, rulesLabel, width, height)
                    } catch (t: Throwable) {
                        android.util.Log.e("MonitorService", "Inference error: ${t.message}", t)
                    }
                }
            }
        )
        cameraSource = camera
        camera.start()

        val mic = MicSource(
            sampleRate = 16_000,
            chunkDurationMs = 100,
            audioCallback = { pcm16, sampleRate, ts ->
                // T3.2: only blackout stops perception; `privacy` still produces barks and
                // behaviour events (they are numbers, not images).
                if (modeController.effectiveMode() == ViewMode.BLACKOUT) {
                    return@MicSource
                }
                val shortArray = ShortArray(pcm16.size / 2)
                ByteBuffer.wrap(pcm16).order(ByteOrder.LITTLE_ENDIAN).asShortBuffer().get(shortArray)
                // I1: a snippet is raw room audio (5s WAV), so it may leave the phone under exactly
                // the same rule as live streaming (AudioStreamGate: Full mode, listener enabled).
                // Privacy was documented as "barks and behaviour only (no audio)": barks keep
                // flowing as numbers, but recording their audio for replay stops entirely.
                val mayUploadSnippets = modeController.shouldStreamAudio()
                if (mayUploadSnippets) {
                    audioSnippetBuffer.push(shortArray)
                }
                val events = pipe.pushAudioPcm16(ts, shortArray)
                events.forEach { event ->
                    val isBarkOrDistress = (event.label == com.btb.ondevice.contracts.AudioLabel.bark ||
                                            event.label == com.btb.ondevice.contracts.AudioLabel.whimper ||
                                            event.label == com.btb.ondevice.contracts.AudioLabel.howl ||
                                            event.label == com.btb.ondevice.contracts.AudioLabel.growl) &&
                                           event.score >= 0.65
                    if (isBarkOrDistress && mayUploadSnippets) {
                        val snipId = "snip_${up.stationId ?: "station"}_${(ts * 1000).toLong()}"
                        val wavBytes = audioSnippetBuffer.captureWavSnippet(5.0)
                        up.uploadAudioSnippet(snipId, wavBytes)
                        up.sendAudio(event.copy(snippetId = snipId, durationS = 5.0))
                    } else {
                        // No snippet recorded or drop pending: the event carries no snippetId, so the
                        // server never points replay at audio that never arrived.
                        up.sendAudio(event)
                    }
                }
                // T3.9: live audio, only in Full mode with a listener (AudioStreamGate), same AudioRecord.
                streamAudioChunk(up, pcm16)
            }
        )
        micSource = mic
        mic.start()

        registerNetworkCallback()
    }

    fun attachCameraPreview(surfaceProvider: Preview.SurfaceProvider) {
        cameraSource?.attachPreview(surfaceProvider)
    }

    fun detachCameraPreview() {
        cameraSource?.detachPreview()
    }

    fun setTorch(enabled: Boolean): Boolean {
        val ok = cameraSource?.setTorch(enabled) ?: false
        val on = cameraSource?.isTorchOn() ?: enabled
        android.os.Handler(android.os.Looper.getMainLooper()).post {
            listener?.onTorchStateChanged(on)
        }
        return ok
    }

    fun toggleTorch(): Boolean {
        val newOn = cameraSource?.toggleTorch() ?: false
        android.os.Handler(android.os.Looper.getMainLooper()).post {
            listener?.onTorchStateChanged(newOn)
        }
        return newOn
    }
    fun isTorchOn(): Boolean = cameraSource?.isTorchOn() ?: false
    fun hasFlashUnit(): Boolean = cameraSource?.hasFlashUnit() ?: false
    fun flipCamera(): Boolean = cameraSource?.flipCamera() ?: false
    fun isBackCamera(): Boolean = cameraSource?.isBack() ?: true

    private fun playTreatChime() {
        try {
            val toneGen = android.media.ToneGenerator(android.media.AudioManager.STREAM_NOTIFICATION, 100)
            toneGen.startTone(android.media.ToneGenerator.TONE_PROP_BEEP2, 350)
            android.os.Handler(android.os.Looper.getMainLooper()).postDelayed({
                try {
                    toneGen.release()
                } catch (_: Exception) {}
            }, 600)
        } catch (e: Exception) {
            Log.w("MonitorService", "Failed to play treat chime: ${e.message}")
        }
    }

    /**
     * T3.9: encode one 100 ms microphone chunk and hand it to the uploader. Runs on the microphone thread
     * after the blackout return and YAMNet, so it never delays perception. Any failure is logged and the
     * chunk skipped: the recorder thread must keep running.
     */
    private fun streamAudioChunk(up: EventUploader, pcm16: ByteArray) {
        if (!modeController.shouldStreamAudio()) return
        if (pcm16.size != AdpcmCodec.PCM16_PAYLOAD_BYTES) return
        try {
            val seq = audioSeq.getAndIncrement() and AUDIO_U32_MASK
            val tMs = android.os.SystemClock.elapsedRealtime() and AUDIO_U32_MASK
            val payload = if (AUDIO_CODEC == AdpcmCodec.CODEC_IMA_ADPCM) {
                ByteBuffer.wrap(pcm16).order(ByteOrder.LITTLE_ENDIAN).asShortBuffer().get(audioShorts)
                AdpcmCodec.encodeBlock(audioShorts)
            } else {
                pcm16
            }
            up.sendAudioChunk(AdpcmCodec.packFrame(AUDIO_CODEC, seq, tMs, payload))
        } catch (t: Throwable) {
            Log.w("MonitorService", "audio chunk skipped: ${t.message}")
        }
    }

    /** True while live audio is actually leaving the phone: the gate is open and the socket is up. */
    fun isAudioStreaming(): Boolean =
        modeController.shouldStreamAudio() && uploader?.connectionState == ConnectionState.CONNECTED

    /** T3.9 listener chime: a low prompt then a beep, distinct from the video chime (ACK, BEEP2) and the treat chime. */
    private fun playListenerChime() {
        try {
            val toneGen = android.media.ToneGenerator(android.media.AudioManager.STREAM_NOTIFICATION, 100)
            toneGen.startTone(android.media.ToneGenerator.TONE_PROP_PROMPT, 200)
            android.os.Handler(android.os.Looper.getMainLooper()).postDelayed({
                try {
                    toneGen.startTone(android.media.ToneGenerator.TONE_PROP_BEEP, 200)
                } catch (_: Exception) {}
            }, 220)
            android.os.Handler(android.os.Looper.getMainLooper()).postDelayed({
                try {
                    toneGen.release()
                } catch (_: Exception) {}
            }, 600)
        } catch (e: Exception) {
            Log.w("MonitorService", "Failed to play listener chime: ${e.message}")
        }
    }

    /** T3.4: two-tone chime (TONE_PROP_ACK then TONE_PROP_BEEP2) when viewer count goes 0 -> >0. */
    private fun playViewerChime() {
        try {
            val toneGen = android.media.ToneGenerator(android.media.AudioManager.STREAM_NOTIFICATION, 100)
            toneGen.startTone(android.media.ToneGenerator.TONE_PROP_ACK, 200)
            android.os.Handler(android.os.Looper.getMainLooper()).postDelayed({
                try {
                    toneGen.startTone(android.media.ToneGenerator.TONE_PROP_BEEP2, 250)
                } catch (_: Exception) {}
            }, 220)
            android.os.Handler(android.os.Looper.getMainLooper()).postDelayed({
                try {
                    toneGen.release()
                } catch (_: Exception) {}
            }, 600)
        } catch (e: Exception) {
            Log.w("MonitorService", "Failed to play viewer chime: ${e.message}")
        }
    }

    /** Multi-sensory soundboard cue + optional flashlight pulse. */
    fun playCue(sound: String, lightPattern: String? = null) {
        try {
            val toneGen = android.media.ToneGenerator(android.media.AudioManager.STREAM_NOTIFICATION, 100)
            when (sound) {
                "squeak" -> {
                    toneGen.startTone(android.media.ToneGenerator.TONE_SUP_RINGTONE, 250)
                }
                "whistle" -> {
                    toneGen.startTone(android.media.ToneGenerator.TONE_DTMF_D, 350)
                }
                "doorbell" -> {
                    toneGen.startTone(android.media.ToneGenerator.TONE_DTMF_1, 200)
                    android.os.Handler(android.os.Looper.getMainLooper()).postDelayed({
                        try {
                            toneGen.startTone(android.media.ToneGenerator.TONE_DTMF_4, 300)
                        } catch (_: Exception) {}
                    }, 220)
                }
                "lullaby" -> {
                    toneGen.startTone(android.media.ToneGenerator.TONE_PROP_BEEP, 300)
                }
                else -> {
                    toneGen.startTone(android.media.ToneGenerator.TONE_PROP_BEEP2, 250)
                }
            }
            android.os.Handler(android.os.Looper.getMainLooper()).postDelayed({
                try {
                    toneGen.release()
                } catch (_: Exception) {}
            }, 800)
        } catch (e: Exception) {
            Log.w("MonitorService", "Failed to play cue sound: ${e.message}")
        }

        if (!lightPattern.isNullOrBlank()) {
            cameraSource?.pulseTorch(lightPattern)
        }
    }

    /** Two-way push-to-talk voice downlink playback with canine hearing protection & pre-chime. */
    fun playVoiceChunk(pcmBytes: ByteArray) {
        if (pcmBytes.isEmpty()) return
        val now = System.currentTimeMillis()
        if (now - lastVoiceChunkMs > 2000L) {
            // Gentle 150ms canine safety pre-chime so dog isn't startled
            try {
                val toneGen = android.media.ToneGenerator(android.media.AudioManager.STREAM_NOTIFICATION, 80)
                toneGen.startTone(android.media.ToneGenerator.TONE_PROP_BEEP2, 150)
                android.os.Handler(android.os.Looper.getMainLooper()).postDelayed({
                    try { toneGen.release() } catch (_: Exception) {}
                }, 300)
            } catch (e: Exception) {
                Log.w("MonitorService", "Failed to play voice pre-chime: ${e.message}")
            }
        }
        lastVoiceChunkMs = now

        try {
            if (voiceAudioTrack == null) {
                val sampleRate = 16000
                val minBufSize = android.media.AudioTrack.getMinBufferSize(
                    sampleRate,
                    android.media.AudioFormat.CHANNEL_OUT_MONO,
                    android.media.AudioFormat.ENCODING_PCM_16BIT
                )
                voiceAudioTrack = android.media.AudioTrack.Builder()
                    .setAudioAttributes(
                        android.media.AudioAttributes.Builder()
                            .setUsage(android.media.AudioAttributes.USAGE_MEDIA)
                            .setContentType(android.media.AudioAttributes.CONTENT_TYPE_SPEECH)
                            .build()
                    )
                    .setAudioFormat(
                        android.media.AudioFormat.Builder()
                            .setEncoding(android.media.AudioFormat.ENCODING_PCM_16BIT)
                            .setSampleRate(sampleRate)
                            .setChannelMask(android.media.AudioFormat.CHANNEL_OUT_MONO)
                            .build()
                    )
                    .setBufferSizeInBytes(maxOf(minBufSize, 4096))
                    .setTransferMode(android.media.AudioTrack.MODE_STREAM)
                    .build()
                voiceAudioTrack?.play()
            }

            // Apply dynamic gain limiter on 16-bit PCM samples to protect pet hearing
            val limited = pcmBytes.clone()
            for (i in 0 until limited.size - 1 step 2) {
                var sample = (limited[i].toInt() and 0xFF) or (limited[i + 1].toInt() shl 8)
                if (sample > 32767) sample -= 65536
                val clamped = sample.coerceIn(-22000, 22000)
                limited[i] = (clamped and 0xFF).toByte()
                limited[i + 1] = ((clamped shr 8) and 0xFF).toByte()
            }

            voiceAudioTrack?.write(limited, 0, limited.size)
        } catch (e: Exception) {
            Log.w("MonitorService", "Failed to play voice chunk: ${e.message}")
        }
    }

    private fun updateUiOverlay(frame: FrameEvent, rules: RulesLabel?, width: Int = 0, height: Int = 0) {
        val l = listener ?: return
        val det = frame.bbox
        val bbox = if (det != null && det.size >= 4 && width > 0 && height > 0) {
            floatArrayOf(
                (det[0] / width).toFloat(),
                (det[1] / height).toFloat(),
                (det[2] / width).toFloat(),
                (det[3] / height).toFloat()
            )
        } else null

        val kpts = frame.bodyKeypoints?.mapValues { entry ->
            val v = entry.value
            if (v != null && v.size >= 3 && width > 0 && height > 0) {
                floatArrayOf((v[0] / width).toFloat(), (v[1] / height).toFloat(), v[2].toFloat())
            } else {
                floatArrayOf(0f, 0f, 0f)
            }
        }

        val overlayState = OverlayState(
            bbox = bbox,
            keypoints = kpts,
            wagRoi = null,
            emotionLabel = rules?.emotion?.name,
            confidence = rules?.confidence?.toFloat() ?: 0f,
            degradeLevel = pipeline?.scheduler?.getLevel()?.name
        )
        // Throttled: at most ~4 UI updates a second, and none while the state is unchanged.
        val due = uiThrottle.offer(android.os.SystemClock.elapsedRealtime(), overlayState) ?: return
        l.onStateUpdate(due)
    }

    /**
     * Load one model runner without letting a single unloadable model abort service
     * start. Returns null (stage stays off) and logs, instead of throwing.
     */
    private fun loadRunnerQuietly(registry: ModelRegistry, name: String): ModelRunner? {
        return try {
            registry.getRunner(name)
        } catch (e: Exception) {
            Log.e("MonitorService", "model '$name' failed to load; stage disabled", e)
            null
        }
    }

    private fun initModelRegistry(): ModelRegistry {
        val manifest = try {
            assets.open("models/manifest.json").bufferedReader().use {
                ModelRegistry.parseManifest(it.readText())
            }
        } catch (_: Exception) {
            ModelRegistry.parseManifest("""{"version":1,"threads":2,"models":{}}""")
        }

        val loader = ModelLoader { name, path, backend, threads ->
            val fd = assets.openFd(path)
            val buffer = fd.createInputStream().channel.map(
                FileChannel.MapMode.READ_ONLY,
                fd.startOffset,
                fd.declaredLength
            )
            LiteRtRunner.create(name, buffer, backend, threads)
        }

        return ModelRegistry(loader, manifest)
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_ID,
                "WagWatch Monitor",
                NotificationManager.IMPORTANCE_LOW
            ).apply {
                description = "Runs on-device perception while capturing camera and audio"
            }
            val approvals = NotificationChannel(
                APPROVAL_CHANNEL_ID,
                "Sharing requests",
                NotificationManager.IMPORTANCE_HIGH
            ).apply {
                description = "A remote viewer asks to raise what the station shares"
            }
            val manager = getSystemService(NotificationManager::class.java)
            manager?.createNotificationChannel(channel)
            manager?.createNotificationChannel(approvals)
        }
    }

    private fun buildNotification(statusText: String): Notification {
        val stopIntent = Intent(this, MonitorService::class.java).apply {
            action = ACTION_STOP
        }
        val stopPendingIntent = PendingIntent.getService(
            this,
            0,
            stopIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or (if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) PendingIntent.FLAG_IMMUTABLE else 0)
        )

        val viewerPart = if (currentVideoViewers > 0) {
            if (currentVideoViewers == 1) "1 viewer watching" else "$currentVideoViewers viewers watching"
        } else {
            null
        }
        val displayText = listOfNotNull(viewerPart, ListenerIndicator.countText(currentAudioViewers), statusText)
            .joinToString(" · ")

        val builder = NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("WagWatch Monitor")
            .setContentText(displayText)
            .setSmallIcon(android.R.drawable.ic_menu_camera)
            .setOngoing(true)
            .addAction(android.R.drawable.ic_menu_close_clear_cancel, "Stop", stopPendingIntent)
            .setPriority(NotificationCompat.PRIORITY_LOW)

        if (currentVideoViewers > 0 || currentAudioViewers > 0) {
            val kickIntent = Intent(this, MonitorService::class.java).apply {
                action = ACTION_KICK_VIEWERS
            }
            val kickPendingIntent = PendingIntent.getService(
                this,
                1,
                kickIntent,
                PendingIntent.FLAG_UPDATE_CURRENT or (if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) PendingIntent.FLAG_IMMUTABLE else 0)
            )
            builder.addAction(android.R.drawable.ic_menu_close_clear_cancel, "Disconnect viewers", kickPendingIntent)
        }

        return builder.build()
    }

    private fun updateNotification(newStatusText: String? = null) {
        if (!isRunning.get()) return
        if (newStatusText != null) currentStatusText = newStatusText
        val nm = getSystemService(NotificationManager::class.java)
        nm?.notify(NOTIFICATION_ID, buildNotification(currentStatusText))
    }

    private fun stopMonitoring() {
        if (!isRunning.compareAndSet(true, false)) return
        isServiceRunning = false
        approvalHandler.removeCallbacks(approvalTimeout)
        approvalHandler.removeCallbacks(raiseExpiry)
        if (modeController.denyPendingRaise()) listener?.onModeApprovalResolved()
        cancelApprovalNotification()

        cameraSource?.stop()
        cameraSource = null

        micSource?.stop()
        micSource = null

        telemetrySampler?.close()
        telemetrySampler = null

        ladderJob?.cancel()
        ladderJob = null
        previewEncoder?.shutdown()
        previewEncoder = null

        executor?.close()
        executor = null

        uploader?.stop()
        uploader = null

        modelRegistry?.close()
        modelRegistry = null
        pipeline = null

        wakeLock?.let {
            if (it.isHeld) it.release()
        }
        wakeLock = null

        try {
            voiceAudioTrack?.stop()
            voiceAudioTrack?.release()
            voiceAudioTrack = null
        } catch (_: Exception) {}

        unregisterNetworkCallback()

        lifecycleRegistry.currentState = Lifecycle.State.DESTROYED
        stopForeground(STOP_FOREGROUND_REMOVE)
    }

    private fun registerNetworkCallback() {
        val cm = getSystemService(Context.CONNECTIVITY_SERVICE) as? android.net.ConnectivityManager ?: return
        val cb = object : android.net.ConnectivityManager.NetworkCallback() {
            override fun onAvailable(network: android.net.Network) {
                uploader?.triggerFastReconnect()
            }
        }
        try {
            cm.registerDefaultNetworkCallback(cb)
            networkCallback = cb
        } catch (_: Exception) {}
    }

    private fun unregisterNetworkCallback() {
        val cm = getSystemService(Context.CONNECTIVITY_SERVICE) as? android.net.ConnectivityManager ?: return
        networkCallback?.let {
            try {
                cm.unregisterNetworkCallback(it)
            } catch (_: Exception) {}
        }
        networkCallback = null
    }

    override fun onDestroy() {
        stopMonitoring()
        super.onDestroy()
    }

    fun getSpooledBytes(): Long = uploader?.spool?.sizeBytes ?: 0L

    companion object {
        const val CHANNEL_ID = "btb_monitor_channel"
        const val NOTIFICATION_ID = 4040
        const val ACTION_START = "com.btb.ondevice.action.START"
        const val ACTION_STOP = "com.btb.ondevice.action.STOP"
        const val ACTION_KICK_VIEWERS = "com.btb.ondevice.action.KICK_VIEWERS"
        const val ACTION_ALLOW_RAISE = "com.btb.ondevice.action.ALLOW_RAISE"
        const val ACTION_KEEP_PRIVATE = "com.btb.ondevice.action.KEEP_PRIVATE"
        const val APPROVAL_CHANNEL_ID = "btb_approval_channel"
        const val APPROVAL_NOTIFICATION_ID = 4041
        const val EXTRA_SERVER_URL = "extra_server_url"
        const val EXTRA_DEVICE_NAME = "extra_device_name"
        const val EXTRA_TOKEN = "extra_token"
        const val EXTRA_STATION_ID = "extra_station_id"
        // The build default (debug: ws:// for the emulator; release: wss://, see the H2 build guard).
        const val DEFAULT_SERVER_URL = com.btb.ondevice.BuildConfig.DEFAULT_INGEST_URL
        /** Set with the service's own running flag, so an activity can check it before it restarts anything. */
        @Volatile
        var isServiceRunning: Boolean = false

        /** T3.9: codec of the live audio frames (AdpcmCodec.CODEC_IMA_ADPCM; PCM16 = 0 is for debugging). */
        const val AUDIO_CODEC = AdpcmCodec.CODEC_IMA_ADPCM
        private const val AUDIO_U32_MASK = 0xFFFFFFFFL
        const val KEY_STATION_CAP = "station_cap"   // T3.2: the cap the person holding the phone allows
    }
}