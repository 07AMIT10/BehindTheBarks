package com.btb.ondevice.bench

import com.btb.ondevice.pipeline.DegradeLevel
import com.google.common.truth.Truth.assertThat
import org.junit.jupiter.api.Test
import java.io.File
import java.nio.file.Files

class MetricsTest {

    @Test
    fun `StageTracker computes mean and p95 correctly`() {
        val tracker = StageTracker(maxSamples = 100, hzWindowS = 3.0)

        // Record 100 samples from 1.0 to 100.0 ms
        for (i in 1..100) {
            tracker.record(i.toDouble(), timestampS = 10.0 + i * 0.01)
        }

        val metrics = tracker.metrics(now = 11.5)
        // Mean of 1..100 is 50.5
        assertThat(metrics.mean_ms).isWithin(0.1).of(50.5)
        // 95th percentile of 1..100 is ~95.0
        assertThat(metrics.p95_ms).isWithin(1.0).of(95.0)
        // 100 events in 1.0s window -> in last 3s window all 100 events -> 100 / 3.0 = 33.3 Hz
        assertThat(metrics.hz).isWithin(0.5).of(33.3)
    }

    @Test
    fun `MetricsTracker aggregates stages and produces valid snapshot and json`() {
        var mockTime = 1000.0
        val tracker = MetricsTracker(clock = { mockTime })

        tracker.recordStage("detect", 12.5, timestampS = mockTime)
        tracker.recordStage("pose", 8.2, timestampS = mockTime)
        tracker.recordStage("face", 5.0, timestampS = mockTime)
        tracker.recordDrop(2)

        val backends = mapOf("detector" to "CPU_XNNPACK", "pose" to "CPU_XNNPACK")
        val snapshot = tracker.snapshot(
            now = mockTime,
            pssKb = 120_000L,
            thermalStatus = 1,
            thermalHeadroom = 0.65f,
            batteryPct = 78,
            charging = true,
            degradeLevel = DegradeLevel.L0_NORMAL,
            backends = backends,
        )

        assertThat(snapshot.ts).isEqualTo(1000.0)
        assertThat(snapshot.queue_drops).isEqualTo(2)
        assertThat(snapshot.pss_kb).isEqualTo(120_000L)
        assertThat(snapshot.thermal_status).isEqualTo(1)
        assertThat(snapshot.thermal_headroom).isWithin(0.01f).of(0.65f)
        assertThat(snapshot.battery_pct).isEqualTo(78)
        assertThat(snapshot.charging).isTrue()
        assertThat(snapshot.degrade_level).isEqualTo("L0_NORMAL")
        assertThat(snapshot.backends).containsEntry("detector", "CPU_XNNPACK")

        assertThat(snapshot.stages.keys).containsExactly("detect", "pose", "face")
        assertThat(snapshot.stages["detect"]!!.mean_ms).isWithin(0.01).of(12.5)

        val jsonString = MetricsTracker.toJson(snapshot)
        assertThat(jsonString).contains(""""queue_drops":2""")
        assertThat(jsonString).contains(""""degrade_level":"L0_NORMAL"""")
        assertThat(jsonString).contains(""""detect"""")
    }

    @Test
    fun `logToJsonl appends lines to file`() {
        val tempDir = Files.createTempDirectory("metrics_test").toFile()
        val logFile = File(tempDir, "metrics.jsonl")

        val tracker = MetricsTracker(clock = { 10.0 })
        tracker.recordStage("rules", 0.5, timestampS = 10.0)
        val snap1 = tracker.snapshot(now = 10.0)
        val snap2 = tracker.snapshot(now = 11.0)

        MetricsTracker.logToJsonl(logFile, snap1)
        MetricsTracker.logToJsonl(logFile, snap2)

        val lines = logFile.readLines()
        assertThat(lines).hasSize(2)
        assertThat(lines[0]).contains(""""ts":10.0""")
        assertThat(lines[1]).contains(""""ts":11.0""")

        tempDir.deleteRecursively()
    }
}
