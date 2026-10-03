package com.btb.ondevice.bench

import com.btb.ondevice.pipeline.DegradeLevel
import kotlinx.serialization.Serializable
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import java.io.File
import java.io.FileWriter
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicLong
import kotlin.math.roundToInt

@Serializable
data class StageMetrics(
    val mean_ms: Double,
    val p95_ms: Double,
    val hz: Double,
)

@Serializable
data class DeviceMetricsSnapshot(
    val ts: Double,
    val stages: Map<String, StageMetrics> = emptyMap(),
    val queue_drops: Long = 0,
    val pss_kb: Long = 0,
    val thermal_status: Int = 0,
    val thermal_headroom: Float = Float.NaN,
    val battery_pct: Int = -1,
    val charging: Boolean = false,
    val degrade_level: String = "L0_NORMAL",
    val backends: Map<String, String> = emptyMap(),
)

class StageTracker(
    private val maxSamples: Int = 100,
    private val hzWindowS: Double = 3.0,
) {
    private val samples = DoubleArray(maxSamples)
    private var sampleCount = 0
    private var insertIdx = 0

    private val timestamps = DoubleArray(maxSamples)
    private var tsCount = 0
    private var tsInsertIdx = 0

    private val lock = Any()

    fun record(durationMs: Double, timestampS: Double) = synchronized(lock) {
        samples[insertIdx] = durationMs
        insertIdx = (insertIdx + 1) % maxSamples
        if (sampleCount < maxSamples) sampleCount++

        timestamps[tsInsertIdx] = timestampS
        tsInsertIdx = (tsInsertIdx + 1) % maxSamples
        if (tsCount < maxSamples) tsCount++
    }

    fun metrics(now: Double): StageMetrics = synchronized(lock) {
        if (sampleCount == 0) {
            return StageMetrics(0.0, 0.0, 0.0)
        }

        var sum = 0.0
        val sorted = DoubleArray(sampleCount)
        for (i in 0 until sampleCount) {
            val v = samples[i]
            sum += v
            sorted[i] = v
        }
        sorted.sort()

        val mean = (sum / sampleCount * 100.0).roundToInt() / 100.0
        val p95Idx = ((sampleCount - 1) * 0.95).toInt()
        val p95 = (sorted[p95Idx] * 100.0).roundToInt() / 100.0

        // Realised Hz over hzWindowS
        val cutoff = now - hzWindowS
        var eventsInWindow = 0
        for (i in 0 until tsCount) {
            if (timestamps[i] >= cutoff) {
                eventsInWindow++
            }
        }
        val hz = if (hzWindowS > 0) ((eventsInWindow / hzWindowS) * 10.0).roundToInt() / 10.0 else 0.0

        return StageMetrics(mean_ms = mean, p95_ms = p95, hz = hz)
    }

    fun clear() = synchronized(lock) {
        sampleCount = 0
        insertIdx = 0
        tsCount = 0
        tsInsertIdx = 0
    }
}

class MetricsTracker(
    private val clock: () -> Double = { System.currentTimeMillis() / 1000.0 },
) {
    private val stages = ConcurrentHashMap<String, StageTracker>()
    private val queueDrops = AtomicLong(0)

    fun recordStage(stage: String, durationMs: Double, timestampS: Double = clock()) {
        val tracker = stages.computeIfAbsent(stage) { StageTracker() }
        tracker.record(durationMs, timestampS)
    }

    fun recordDrop(count: Long = 1) {
        queueDrops.addAndGet(count)
    }

    fun setDrops(total: Long) {
        queueDrops.set(total)
    }

    fun snapshot(
        now: Double = clock(),
        pssKb: Long = 0,
        thermalStatus: Int = 0,
        thermalHeadroom: Float = Float.NaN,
        batteryPct: Int = -1,
        charging: Boolean = false,
        degradeLevel: DegradeLevel = DegradeLevel.L0_NORMAL,
        backends: Map<String, String> = emptyMap(),
    ): DeviceMetricsSnapshot {
        val stageMap = mutableMapOf<String, StageMetrics>()
        for ((name, tracker) in stages) {
            stageMap[name] = tracker.metrics(now)
        }

        return DeviceMetricsSnapshot(
            ts = now,
            stages = stageMap,
            queue_drops = queueDrops.get(),
            pss_kb = pssKb,
            thermal_status = thermalStatus,
            thermal_headroom = thermalHeadroom,
            battery_pct = batteryPct,
            charging = charging,
            degrade_level = degradeLevel.name,
            backends = backends,
        )
    }

    companion object {
        private val json = Json {
            prettyPrint = false
            ignoreUnknownKeys = true
            encodeDefaults = true
            allowSpecialFloatingPointValues = true
        }

        fun toJson(snapshot: DeviceMetricsSnapshot): String {
            return json.encodeToString(snapshot)
        }

        fun logToJsonl(file: File, snapshot: DeviceMetricsSnapshot) {
            file.parentFile?.mkdirs()
            FileWriter(file, true).use { writer ->
                writer.write(toJson(snapshot))
                writer.write("\n")
            }
        }
    }
}
