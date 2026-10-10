package com.btb.ondevice.capture

import com.btb.ondevice.bench.MetricsTracker
import com.btb.ondevice.net.ConnectionState
import com.btb.ondevice.net.EventUploader
import com.btb.ondevice.pipeline.DegradeLevel
import com.btb.ondevice.pipeline.ThermalProvider
import com.google.common.truth.Truth.assertThat
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.double
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.WebSocket
import org.junit.jupiter.api.Test
import java.util.concurrent.atomic.AtomicInteger

@OptIn(ExperimentalCoroutinesApi::class)
class TelemetrySamplerTest {

    private class FakeBatteryProvider(
        var pct: Int = 85,
        var charging: Boolean = true,
        var plugged: String = "usb",
        var tempC: Double = 31.5,
    ) : BatteryProvider {
        override fun getBatteryInfo() = BatteryInfo(pct, charging, plugged, tempC)
    }

    private class FakeThermalProvider(
        var status: Int = 0,
        var headroom: Float = 0.5f,
    ) : ThermalProvider {
        override fun getThermalStatus() = status
        override fun getThermalHeadroom(forecastSeconds: Int) = headroom
        override fun getBatteryPercent() = 85
        override fun isCharging() = true
    }

    private class FakeMemoryProvider(
        var memMb: Long = 256L,
    ) : com.btb.ondevice.pipeline.MemoryProvider {
        override fun isLowMemory(): Boolean = false
        override fun getPssKb(): Long = memMb * 1024L
    }

    @Test
    fun `sample generates valid JSON envelope with expected keys`() {
        val bat = FakeBatteryProvider(pct = 92, charging = true, plugged = "usb", tempC = 30.5)
        val therm = FakeThermalProvider(status = 1, headroom = 0.8f)
        val mem = FakeMemoryProvider(memMb = 310L)
        val tracker = MetricsTracker(clock = { 1000.0 })
        tracker.recordStage("detector", 15.2, 1000.0)
        tracker.recordStage("pose", 12.0, 1000.0)

        val dispatcher = StandardTestDispatcher()
        val scope = TestScope(dispatcher)

        val sampler = TelemetrySampler(
            uploader = { null },
            batteryProvider = bat,
            thermalProvider = therm,
            memoryProvider = mem,
            metricsTracker = { tracker },
            degradeLevel = { DegradeLevel.L1_WARM },
            clock = { 1700000000.0 },
            scope = scope,
        )

        val jsonStr = sampler.sample()
        val root = Json.parseToJsonElement(jsonStr).jsonObject
        assertThat(root["type"]?.jsonPrimitive?.content).isEqualTo("telemetry")

        val data = root["data"]?.jsonObject
        assertThat(data).isNotNull()
        assertThat(data!!["ts"]?.jsonPrimitive?.double).isEqualTo(1700000000.0)
        assertThat(data["battery_pct"]?.jsonPrimitive?.int).isEqualTo(92)
        assertThat(data["charging"]?.jsonPrimitive?.boolean).isTrue()
        assertThat(data["plugged"]?.jsonPrimitive?.content).isEqualTo("usb")
        assertThat(data["temp_c"]?.jsonPrimitive?.double).isEqualTo(30.5)
        assertThat(data["thermal_status"]?.jsonPrimitive?.int).isEqualTo(1)
        assertThat(data["mem_mb"]?.jsonPrimitive?.int).isEqualTo(310)
        assertThat(data["degrade_level"]?.jsonPrimitive?.content).isEqualTo("L1_WARM")

        val infMs = data["inference_ms"]?.jsonObject
        assertThat(infMs).isNotNull()
        assertThat(infMs!!["detector"]?.jsonPrimitive?.double).isEqualTo(15.2)
        assertThat(infMs["pose"]?.jsonPrimitive?.double).isEqualTo(12.0)
        assertThat(infMs["face"]?.jsonPrimitive?.double).isEqualTo(0.0)
    }

    @Test
    fun `sample handles NaN thermal headroom gracefully as null`() {
        val bat = FakeBatteryProvider()
        val therm = FakeThermalProvider(headroom = Float.NaN)
        val mem = FakeMemoryProvider()

        val dispatcher = StandardTestDispatcher()
        val scope = TestScope(dispatcher)

        val sampler = TelemetrySampler(
            uploader = { null },
            batteryProvider = bat,
            thermalProvider = therm,
            memoryProvider = mem,
            metricsTracker = { null },
            scope = scope,
        )

        val jsonStr = sampler.sample()
        val root = Json.parseToJsonElement(jsonStr).jsonObject
        val data = root["data"]?.jsonObject
        assertThat(data!!["thermal_headroom"]).isEqualTo(kotlinx.serialization.json.JsonNull)
    }

    @Test
    fun `sampleAndSend returns false when uploader is null or disconnected`() {
        val bat = FakeBatteryProvider()
        val therm = FakeThermalProvider()
        val mem = FakeMemoryProvider()

        val dispatcher = StandardTestDispatcher()
        val scope = TestScope(dispatcher)

        val sampler = TelemetrySampler(
            uploader = { null },
            batteryProvider = bat,
            thermalProvider = therm,
            memoryProvider = mem,
            metricsTracker = { null },
            scope = scope,
        )

        assertThat(sampler.sampleAndSend()).isFalse()
    }

    @Test
    fun `sampler starts periodic job and terminates on close`() = runTest {
        val bat = FakeBatteryProvider()
        val therm = FakeThermalProvider()
        val mem = FakeMemoryProvider()
        val sampleCalls = AtomicInteger(0)

        val sampler = object : TelemetrySampler(
            uploader = { null },
            batteryProvider = bat,
            thermalProvider = therm,
            memoryProvider = mem,
            metricsTracker = { null },
            sampleIntervalMs = 1000L,
            scope = this,
        ) {
            override fun sampleAndSend(): Boolean {
                sampleCalls.incrementAndGet()
                return true
            }
        }

        sampler.start()
        testScheduler.advanceTimeBy(100)
        assertThat(sampleCalls.get()).isEqualTo(1)

        testScheduler.advanceTimeBy(1000)
        assertThat(sampleCalls.get()).isEqualTo(2)

        sampler.close()
        testScheduler.advanceTimeBy(2000)
        assertThat(sampleCalls.get()).isEqualTo(2)
    }
}
