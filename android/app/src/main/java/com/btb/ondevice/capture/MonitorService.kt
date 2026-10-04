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
import com.btb.ondevice.audio.AudioPipeline
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
import com.btb.ondevice.pipeline.AndroidMemoryProvider
import com.btb.ondevice.pipeline.AndroidThermalProvider
import com.btb.ondevice.pipeline.InferenceExecutor
import com.btb.ondevice.pipeline.MemoryGuard
import com.btb.ondevice.pipeline.OnDevicePipeline
import com.btb.ondevice.pipeline.ThermalGovernor
import com.btb.ondevice.ui.OverlayState
import com.btb.ondevice.vision.DogDetector
import com.btb.ondevice.vision.FaceLandmarker
import com.btb.ondevice.vision.PoseEstimator
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
    private val lastPreviewSendMs = AtomicLong(0)
    private var listener: MonitorServiceListener? = null
    private val privacyMode = AtomicBoolean(false)
    private var privacySlateJpeg: ByteArray? = null
    private val isScanningQr = AtomicBoolean(false)
    private var networkCallback: android.net.ConnectivityManager.NetworkCallback? = null

    fun setScanningQr(enabled: Boolean) {
        isScanningQr.set(enabled)
    }

    fun isScanningQr(): Boolean = isScanningQr.get()

    private fun getOrCreatePrivacySlate(): ByteArray {
        privacySlateJpeg?.let { return it }
        val bmp = Bitmap.createBitmap(320, 240, Bitmap.Config.ARGB_8888)
        val canvas = android.graphics.Canvas(bmp)
        canvas.drawColor(android.graphics.Color.rgb(22, 24, 30))
        val paint = android.graphics.Paint().apply {
            color = android.graphics.Color.rgb(239, 68, 68)
            textSize = 20f
            isAntiAlias = true
            textAlign = android.graphics.Paint.Align.CENTER
        }
        canvas.drawText("PRIVACY MODE ACTIVE", 160f, 110f, paint)
        paint.color = android.graphics.Color.rgb(156, 163, 175)
        paint.textSize = 12f
        canvas.drawText("Camera & Mic Muted", 160f, 140f, paint)
        val out = ByteArrayOutputStream()
        bmp.compress(Bitmap.CompressFormat.JPEG, 75, out)
        bmp.recycle()
        val bytes = out.toByteArray()
        privacySlateJpeg = bytes
        return bytes
    }

    fun setPrivacyMode(enabled: Boolean): Boolean {
        privacyMode.set(enabled)
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

    fun isPrivacyMode(): Boolean = privacyMode.get()
    fun togglePrivacyMode(): Boolean = setPrivacyMode(!privacyMode.get())

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

        val prefs = getSharedPreferences("btb_station_prefs", Context.MODE_PRIVATE)
        val defaultUrl = prefs.getString("server_url", DEFAULT_SERVER_URL) ?: DEFAULT_SERVER_URL
        val defaultDevice = prefs.getString("device_name", Build.MODEL) ?: Build.MODEL
        val defaultToken = prefs.getString("access_token", "")?.takeIf { it.isNotBlank() }

        val serverUrl = intent?.getStringExtra(EXTRA_SERVER_URL)
            ?: intent?.getStringExtra("server_url")
            ?: defaultUrl
        val deviceName = intent?.getStringExtra(EXTRA_DEVICE_NAME)
            ?: intent?.getStringExtra("device_name")
            ?: defaultDevice
        val token = intent?.getStringExtra(EXTRA_TOKEN)
            ?: intent?.getStringExtra("token")
            ?: defaultToken

        startMonitoring(serverUrl, deviceName, token)
        return START_STICKY
    }

    private fun startMonitoring(serverUrl: String, deviceName: String, token: String? = null) {
        if (!isRunning.compareAndSet(false, true)) return

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
            spool = spool,
            listener = object : EventUploaderListener {
                override fun onStateChanged(state: ConnectionState) {
                    listener?.onConnectionUpdate(state)
                }
                override fun onTreatDownlink(ts: Double) {
                    pipe.pushTreat(ts)
                    playTreatChime()
                }
                override fun onTorchDownlink(enabled: Boolean) {
                    cameraSource?.setTorch(enabled)
                }
                override fun onFlipDownlink() {
                    cameraSource?.flipCamera()
                }
                override fun onPrivacyDownlink(enabled: Boolean) {
                    setPrivacyMode(enabled)
                }
            }
        )
        uploader = up
        up.start()

        // 6. Initialize InferenceExecutor
        val exec = InferenceExecutor()
        executor = exec

        // 7. Initialize CameraSource & MicSource
        val camera = CameraSource(
            context = this,
            lifecycleOwner = this,
            targetWidth = 640,
            targetHeight = 480,
            targetFps = 15,
            frameCallback = { rgba, width, height, rotation, ts ->
                if (privacyMode.get()) {
                    val nowMs = System.currentTimeMillis()
                    if (nowMs - lastPreviewSendMs.get() >= 1000) {
                        lastPreviewSendMs.set(nowMs)
                        try {
                            up.sendPreview(getOrCreatePrivacySlate())
                        } catch (_: Exception) {}
                    }
                    return@CameraSource
                }

                if (isScanningQr.get()) {
                    val qrText = com.btb.ondevice.util.QrDecoder.decodeRgba(rgba, width, height)
                    if (qrText != null) {
                        isScanningQr.set(false)
                        listener?.onQrCodeDetected(qrText)
                    }
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
                    // Dashboard video, best-effort at ~2 Hz. JPEG encode stays on this
                    // background thread; sendPreview applies its own rate limit and
                    // backpressure drop, so this never blocks inference.
                    val nowMs = System.currentTimeMillis()
                    if (nowMs - lastPreviewSendMs.get() >= 500) {
                        lastPreviewSendMs.set(nowMs)
                        try {
                            encodePreviewJpeg(rgba, width, height, rotation)?.let { jpeg ->
                                up.sendPreview(jpeg)
                            }
                        } catch (t: Throwable) {
                            android.util.Log.e("MonitorService", "Preview error: ${t.message}", t)
                        }
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
                if (privacyMode.get()) {
                    return@MicSource
                }
                val shortArray = ShortArray(pcm16.size / 2)
                ByteBuffer.wrap(pcm16).order(ByteOrder.LITTLE_ENDIAN).asShortBuffer().get(shortArray)
                val events = pipe.pushAudioPcm16(ts, shortArray)
                events.forEach { up.sendAudio(it) }
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

    fun setTorch(enabled: Boolean): Boolean = cameraSource?.setTorch(enabled) ?: false
    fun toggleTorch(): Boolean = cameraSource?.toggleTorch() ?: false
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
        l.onStateUpdate(overlayState)
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

    /**
     * Downscale an RGBA frame to 320 px (long edge) and encode a dashboard preview JPEG.
     * Returns null when the buffer does not hold a full frame. Caller throttles.
     */
    private fun encodePreviewJpeg(
        rgba: ByteArray,
        width: Int,
        height: Int,
        rotationDegrees: Int = 0,
    ): ByteArray? {
        if (width < 2 || height < 2 || rgba.size < width * height * 4) return null
        val full = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888)
        full.copyPixelsFromBuffer(ByteBuffer.wrap(rgba))
        val targetW: Int
        val targetH: Int
        if (width >= height) {
            targetW = 320
            targetH = (320f * height / width).roundToInt().coerceAtLeast(2)
        } else {
            targetW = (320f * width / height).roundToInt().coerceAtLeast(2)
            targetH = 320
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
        val out = ByteArrayOutputStream(32 * 1024)
        upright.compress(Bitmap.CompressFormat.JPEG, 60, out)
        upright.recycle()
        return out.toByteArray().takeIf { it.isNotEmpty() }
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
                "Behind the Barks Monitor",
                NotificationManager.IMPORTANCE_LOW
            ).apply {
                description = "Runs on-device perception while capturing camera and audio"
            }
            val manager = getSystemService(NotificationManager::class.java)
            manager?.createNotificationChannel(channel)
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

        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("Behind the Barks Monitor")
            .setContentText(statusText)
            .setSmallIcon(android.R.drawable.ic_menu_camera)
            .setOngoing(true)
            .addAction(android.R.drawable.ic_menu_close_clear_cancel, "Stop", stopPendingIntent)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .build()
    }

    private fun stopMonitoring() {
        if (!isRunning.compareAndSet(true, false)) return

        cameraSource?.stop()
        cameraSource = null

        micSource?.stop()
        micSource = null

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
        const val EXTRA_SERVER_URL = "extra_server_url"
        const val EXTRA_DEVICE_NAME = "extra_device_name"
        const val EXTRA_TOKEN = "extra_token"
        const val DEFAULT_SERVER_URL = "ws://10.0.2.2:8000/ingest-events"
    }
}