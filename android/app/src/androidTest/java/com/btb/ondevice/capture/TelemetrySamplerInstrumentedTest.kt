package com.btb.ondevice.capture

import android.util.Log
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.btb.ondevice.pipeline.AndroidThermalProvider
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

/** The real battery and thermal providers on the phone, the same ones TelemetrySampler reads. */
@RunWith(AndroidJUnit4::class)
class TelemetrySamplerInstrumentedTest {
    private val context = InstrumentationRegistry.getInstrumentation().targetContext

    @Test
    fun batteryProviderReturnsPlausibleValues() {
        val info = AndroidBatteryProvider(context).getBatteryInfo()
        Log.i(
            "BTB-AT",
            "battery_pct=${info.pct} charging=${info.charging} plugged=${info.plugged} temp_c=${info.tempC}",
        )
        assertTrue("battery_pct ${info.pct} outside 0..100", info.pct in 0..100)
        assertTrue("temp_c ${info.tempC} outside -20..90", info.tempC in -20.0..90.0)
    }

    @Test
    fun thermalProviderReturnsStatusInRange() {
        val provider = AndroidThermalProvider(context)
        val status = provider.getThermalStatus()
        val headroom = provider.getThermalHeadroom(0)
        Log.i("BTB-AT", "thermal_status=$status thermal_headroom=$headroom")
        assertTrue("thermal status $status outside 0..6", status in 0..6)
    }
}
