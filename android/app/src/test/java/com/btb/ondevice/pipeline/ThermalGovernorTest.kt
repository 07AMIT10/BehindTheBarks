package com.btb.ondevice.pipeline

import com.google.common.truth.Truth.assertThat
import org.junit.jupiter.api.Test

class ThermalGovernorTest {

    private class FakeThermalProvider(
        var status: Int = 0,
        var headroom: Float = 0.5f,
        var batteryPct: Int = 80,
        var charging: Boolean = true,
    ) : ThermalProvider {
        override fun getThermalStatus(): Int = status
        override fun getThermalHeadroom(forecastSeconds: Int): Float = headroom
        override fun getBatteryPercent(): Int = batteryPct
        override fun isCharging(): Boolean = charging
    }

    @Test
    fun `thermal ladder escalates levels based on status and headroom`() {
        var mockTime = 100.0
        val provider = FakeThermalProvider()
        val governor = ThermalGovernor(provider, clock = { mockTime }, pollIntervalS = 10.0, hysteresisS = 30.0)

        // Initial normal state
        assertThat(governor.poll()).isEqualTo(DegradeLevel.L0_NORMAL)

        // Moderate heat / headroom 0.86 -> L1 Warm
        mockTime += 15.0
        provider.headroom = 0.86f
        provider.status = 2 // MODERATE
        assertThat(governor.poll()).isEqualTo(DegradeLevel.L1_WARM)

        // Severe heat / headroom 0.96 -> L2 Hot (after 30s hysteresis)
        mockTime += 35.0
        provider.headroom = 0.96f
        provider.status = 3 // SEVERE
        assertThat(governor.poll()).isEqualTo(DegradeLevel.L2_HOT)

        // Critical status -> L3 Critical
        mockTime += 35.0
        provider.status = 4 // CRITICAL
        assertThat(governor.poll()).isEqualTo(DegradeLevel.L3_CRITICAL)
    }

    @Test
    fun `low battery under 15 percent and not charging triggers L3 critical`() {
        var mockTime = 100.0
        val provider = FakeThermalProvider(status = 0, headroom = 0.5f, batteryPct = 12, charging = false)
        val governor = ThermalGovernor(provider, clock = { mockTime })

        assertThat(governor.poll()).isEqualTo(DegradeLevel.L3_CRITICAL)
    }

    @Test
    fun `hysteresis prevents premature de-escalation`() {
        var mockTime = 100.0
        val provider = FakeThermalProvider(status = 3, headroom = 0.96f)
        val governor = ThermalGovernor(provider, clock = { mockTime }, pollIntervalS = 10.0, hysteresisS = 30.0)

        // Escalates to L2
        governor.poll()
        assertThat(governor.currentLevel).isEqualTo(DegradeLevel.L2_HOT)

        // Headroom quickly drops to cool 10s later
        mockTime += 10.0
        provider.headroom = 0.4f
        provider.status = 0

        // Hysteresis window (30s) not elapsed (only 10s passed) -> stays L2 Hot
        assertThat(governor.poll()).isEqualTo(DegradeLevel.L2_HOT)

        // After 35s (> 30s hysteresis) -> relaxes to L0 Normal
        mockTime += 25.0
        assertThat(governor.poll()).isEqualTo(DegradeLevel.L0_NORMAL)
    }
}
