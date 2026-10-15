package com.btb.ondevice.capture

import android.app.ActivityManager
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.BatteryManager
import com.btb.ondevice.bench.MetricsTracker
import com.btb.ondevice.net.EventUploader
import com.btb.ondevice.pipeline.DegradeLevel
import com.btb.ondevice.pipeline.MemoryProvider
import com.btb.ondevice.pipeline.ThermalProvider
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonObject
import java.io.Closeable

data class BatteryInfo(
    val pct: Int,
    val charging: Boolean,
    val plugged: String, // "usb", "ac", "wireless", "none"
    val tempC: Double,
)

interface BatteryProvider {
    fun getBatteryInfo(): BatteryInfo
}

class AndroidBatteryProvider(private val context: Context) : BatteryProvider {
    override fun getBatteryInfo(): BatteryInfo {
        val intent = context.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
            ?: return BatteryInfo(pct = -1, charging = false, plugged = "none", tempC = 0.0)

        val level = intent.getIntExtra(BatteryManager.EXTRA_LEVEL, -1)
        val scale = intent.getIntExtra(BatteryManager.EXTRA_SCALE, -1)
        val pct = if (level >= 0 && scale > 0) (level * 100) / scale else -1

        val status = intent.getIntExtra(BatteryManager.EXTRA_STATUS, -1)
        val charging = status == BatteryManager.BATTERY_STATUS_CHARGING ||
                status == BatteryManager.BATTERY_STATUS_FULL

        val plug = intent.getIntExtra(BatteryManager.EXTRA_PLUGGED, -1)
        val plugged = when (plug) {
            BatteryManager.BATTERY_PLUGGED_USB -> "usb"
            BatteryManager.BATTERY_PLUGGED_AC -> "ac"
            BatteryManager.BATTERY_PLUGGED_WIRELESS -> "wireless"
            else -> if (charging) "other" else "none"
        }

        val rawTemp = intent.getIntExtra(BatteryManager.EXTRA_TEMPERATURE, 0)
        val tempC = rawTemp / 10.0

        return BatteryInfo(pct = pct, charging = charging, plugged = plugged, tempC = tempC)
    }
}

open class TelemetrySampler(
    private val uploader: () -> EventUploader?,
    private val batteryProvider: BatteryProvider,
    private val thermalProvider: ThermalProvider,
    private val memoryProvider: MemoryProvider,
    private val metricsTracker: () -> MetricsTracker?,
    private val degradeLevel: () -> DegradeLevel = { DegradeLevel.L0_NORMAL },
    private val previewEncoder: () -> PreviewEncoder? = { null },
    private val previewRung: () -> Int = { 0 },
    /** T3.9: true while live audio is leaving the phone right now. */
    private val audioStreaming: () -> Boolean = { false },
    private val sampleIntervalMs: Long = 10_000L,
    private val clock: () -> Double = { System.currentTimeMillis() / 1000.0 },
    private val scope: CoroutineScope,
) : Closeable {
    private var job: Job? = null

    fun start() {
        job?.cancel()
        job = scope.launch {
            while (isActive) {
                sampleAndSend()
                delay(sampleIntervalMs)
            }
        }
    }

    open fun sampleAndSend(): Boolean {
        val up = uploader() ?: return false
        val envelope = sample()
        return up.sendTelemetry(envelope)
    }

    fun sample(): String {
        val now = clock()
        val bat = batteryProvider.getBatteryInfo()
        val thermalStatus = thermalProvider.getThermalStatus()
        val headroom = thermalProvider.getThermalHeadroom(0)
        val memMb = memoryProvider.getPssKb() / 1024L
        val up = uploader()
        val rtt = up?.lastRttMs ?: 0L
        val queueBytes = up?.queueBytes() ?: 0L
        val spoolBytes = up?.spool?.sizeBytes ?: 0L
        val previewDropped = up?.previewDroppedBackpressure?.get() ?: 0L
        val audioFrames = up?.audioFramesSent?.get() ?: 0L
        val audioDropped = up?.audioDroppedBackpressure?.get() ?: 0L
        val pe = previewEncoder()
        val previewFps = pe?.config?.fps ?: 0.0
        val degrade = degradeLevel().name

        // Inference timings from metricsTracker
        val tracker = metricsTracker()
        val snap = tracker?.snapshot(now)

        return buildJsonObject {
            put("type", "telemetry")
            putJsonObject("data") {
                put("ts", now)
                put("battery_pct", bat.pct)
                put("charging", bat.charging)
                put("plugged", bat.plugged)
                put("temp_c", bat.tempC)
                put("thermal_status", thermalStatus)
                if (headroom.isNaN()) {
                    put("thermal_headroom", null as String?)
                } else {
                    put("thermal_headroom", headroom)
                }
                putJsonObject("inference_ms") {
                    val stages = snap?.stages ?: emptyMap()
                    put("detector", stages["detector"]?.mean_ms ?: 0.0)
                    put("pose", stages["pose"]?.mean_ms ?: 0.0)
                    put("face", stages["face"]?.mean_ms ?: 0.0)
                    put("yamnet", stages["yamnet"]?.mean_ms ?: 0.0)
                }
                put("preview_kbps", 0.0)
                put("preview_fps", previewFps)
                put("preview_dropped", previewDropped)
                put("ws_rtt_ms", rtt)
                put("queue_bytes", queueBytes)
                put("spool_bytes", spoolBytes)
                put("mem_mb", memMb)
                put("degrade_level", degrade)
                put("ladder_rung", previewRung())
                put("audio_streaming", audioStreaming())
                put("audio_frames_sent", audioFrames)
                put("audio_dropped_backpressure", audioDropped)
            }
        }.toString()
    }

    override fun close() {
        job?.cancel()
    }
}
