package com.btb.ondevice.capture

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Binder
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import androidx.camera.core.Preview
import androidx.core.app.NotificationCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleOwner
import androidx.lifecycle.LifecycleRegistry
import com.btb.ondevice.config.DataConfig
import com.btb.ondevice.contracts.AudioEvent
import com.btb.ondevice.contracts.FrameEvent
import com.btb.ondevice.contracts.RulesLabel
import com.btb.ondevice.ml.LiteRtRunner
import com.btb.ondevice.ml.ModelLoader
import com.btb.ondevice.ml.ModelRegistry
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
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.channels.FileChannel
import java.util.concurrent.atomic.AtomicBoolean

interface MonitorServiceListener {
    fun onStateUpdate(overlayState: OverlayState) {}
    fun onConnectionUpdate(state: ConnectionState) {}
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
    private var listener: MonitorServiceListener? = null

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

        val serverUrl = intent?.getStringExtra(EXTRA_SERVER_URL) ?: "ws://10.0.2.2:8000/ingest-events"
        val deviceName = intent?.getStringExtra(EXTRA_DEVICE_NAME) ?: Build.MODEL

        startMonitoring(serverUrl, deviceName)
        return START_STICKY
    }

    private fun startMonitoring(serverUrl: String, deviceName: String) {
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

        // 4. Initialize Thermal Governor & Memory Guard
        val thermal = ThermalGovernor(AndroidThermalProvider(this))
        thermalGovernor = thermal
        val memGuard = MemoryGuard(AndroidMemoryProvider(this), registry)
        memoryGuard = memGuard

        // 5. Initialize EventUploader
        val spoolDir = File(filesDir, "spool")
        val spool = EventSpool(spoolDir)
        val pipe = OnDevicePipeline(config = config)
        pipeline = pipe

        val up = EventUploader(
            serverUrl = serverUrl,
            deviceName = deviceName,
            spool = spool,
            listener = object : EventUploaderListener {
                override fun onStateChanged(state: ConnectionState) {
                    listener?.onConnectionUpdate(state)
                }
                override fun onTreatDownlink(ts: Double) {
                    pipe.pushTreat(ts)
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
                // Check thermal governor and memory guard
                val deg = thermal.poll()
                pipe.scheduler.setLevel(deg)
                memGuard.checkMemory()

                val buffer = ByteBuffer.wrap(rgba)
                exec.submitLatest {
                    val (frameEvent, rulesLabel) = pipe.processFrame(ts, width, height, buffer)
                    up.sendFrame(frameEvent)
                    if (rulesLabel != null) {
                        up.sendRules(rulesLabel)
                    }
                    updateUiOverlay(frameEvent, rulesLabel)
                }
            }
        )
        cameraSource = camera
        camera.start()

        val mic = MicSource(
            sampleRate = 16_000,
            chunkDurationMs = 100,
            audioCallback = { pcm16, sampleRate, ts ->
                val shortArray = ShortArray(pcm16.size / 2)
                ByteBuffer.wrap(pcm16).order(ByteOrder.LITTLE_ENDIAN).asShortBuffer().get(shortArray)
                val events = pipe.pushAudioPcm16(ts, shortArray)
                events.forEach { up.sendAudio(it) }
            }
        )
        micSource = mic
        mic.start()
    }

    fun attachCameraPreview(surfaceProvider: Preview.SurfaceProvider) {
        cameraSource?.attachPreview(surfaceProvider)
    }

    fun detachCameraPreview() {
        cameraSource?.detachPreview()
    }

    private fun updateUiOverlay(frame: FrameEvent, rules: RulesLabel?) {
        val l = listener ?: return
        val det = frame.bbox
        val bbox = if (det != null && det.size >= 4) {
            floatArrayOf(det[0].toFloat(), det[1].toFloat(), det[2].toFloat(), det[3].toFloat())
        } else null

        val kpts = frame.bodyKeypoints?.mapValues { entry ->
            val v = entry.value
            if (v != null && v.size >= 3) {
                floatArrayOf(v[0].toFloat(), v[1].toFloat(), v[2].toFloat())
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

        lifecycleRegistry.currentState = Lifecycle.State.DESTROYED
        stopForeground(STOP_FOREGROUND_REMOVE)
    }

    override fun onDestroy() {
        stopMonitoring()
        super.onDestroy()
    }

    companion object {
        const val CHANNEL_ID = "btb_monitor_channel"
        const val NOTIFICATION_ID = 4040
        const val ACTION_START = "com.btb.ondevice.action.START"
        const val ACTION_STOP = "com.btb.ondevice.action.STOP"
        const val EXTRA_SERVER_URL = "extra_server_url"
        const val EXTRA_DEVICE_NAME = "extra_device_name"
    }
}