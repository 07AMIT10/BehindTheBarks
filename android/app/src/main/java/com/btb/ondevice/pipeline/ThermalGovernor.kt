package com.btb.ondevice.pipeline
 
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.BatteryManager
import android.os.Build
import android.os.PowerManager

interface ThermalProvider {
    fun getThermalStatus(): Int // 0=NONE, 1=LIGHT, 2=MODERATE, 3=SEVERE, 4=CRITICAL, 5=EMERGENCY, 6=SHUTDOWN
    fun getThermalHeadroom(forecastSeconds: Int): Float // NaN if unsupported
    fun getBatteryPercent(): Int
    fun isCharging(): Boolean
}

class AndroidThermalProvider(private val context: Context) : ThermalProvider {
    private val powerManager by lazy { context.getSystemService(Context.POWER_SERVICE) as? PowerManager }

    override fun getThermalStatus(): Int {
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            powerManager?.currentThermalStatus ?: 0
        } else {
            0
        }
    }

    override fun getThermalHeadroom(forecastSeconds: Int): Float {
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            powerManager?.getThermalHeadroom(forecastSeconds) ?: Float.NaN
        } else {
            Float.NaN
        }
    }

    override fun getBatteryPercent(): Int {
        val bm = context.getSystemService(Context.BATTERY_SERVICE) as? BatteryManager
        val pct = bm?.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY) ?: -1
        if (pct in 0..100) return pct

        val intent = context.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED)) ?: return -1
        val level = intent.getIntExtra(BatteryManager.EXTRA_LEVEL, -1)
        val scale = intent.getIntExtra(BatteryManager.EXTRA_SCALE, -1)
        return if (level >= 0 && scale > 0) (level * 100) / scale else -1
    }

    override fun isCharging(): Boolean {
        val intent = context.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED)) ?: return false
        val status = intent.getIntExtra(BatteryManager.EXTRA_STATUS, -1)
        return status == BatteryManager.BATTERY_STATUS_CHARGING || status == BatteryManager.BATTERY_STATUS_FULL
    }
}

class ThermalGovernor(
    private val provider: ThermalProvider,
    private val clock: () -> Double = { System.currentTimeMillis() / 1000.0 },
    val pollIntervalS: Double = 10.0,
    val hysteresisS: Double = 30.0,
) {
    var currentLevel: DegradeLevel = DegradeLevel.L0_NORMAL
        private set

    private var lastPollTs = -1.0
    private var lastLevelChangeTs = 0.0

    fun poll(): DegradeLevel {
        val now = clock()
        if (lastPollTs >= 0 && (now - lastPollTs) < pollIntervalS) {
            return currentLevel
        }
        lastPollTs = now

        val status = provider.getThermalStatus()
        val headroom = provider.getThermalHeadroom(10)
        val batteryPct = provider.getBatteryPercent()
        val charging = provider.isCharging()

        val candidateLevel = computeTargetLevel(status, headroom, batteryPct, charging)

        // Hysteresis: only allow degrade ladder transition if at least hysteresisS seconds have passed
        if (candidateLevel != currentLevel) {
            if (lastLevelChangeTs == 0.0 || (now - lastLevelChangeTs) >= hysteresisS) {
                currentLevel = candidateLevel
                lastLevelChangeTs = now
            }
        }

        return currentLevel
    }

    private fun computeTargetLevel(
        status: Int,
        headroom: Float,
        batteryPct: Int,
        charging: Boolean,
    ): DegradeLevel {
        // L3 Critical: status >= 4 (CRITICAL) or (battery < 15% and not charging)
        if (status >= 4 || (batteryPct < 15 && !charging)) {
            return DegradeLevel.L3_CRITICAL
        }
        // L2 Hot: status >= 3 (SEVERE) or (headroom is valid and >= 0.95)
        if (status >= 3 || (!headroom.isNaN() && headroom >= 0.95f)) {
            return DegradeLevel.L2_HOT
        }
        // L1 Warm: status >= 2 (MODERATE) or (headroom is valid and >= 0.85)
        if (status >= 2 || (!headroom.isNaN() && headroom >= 0.85f)) {
            return DegradeLevel.L1_WARM
        }
        // L0 Normal: headroom < 0.75 and status <= 1 (LIGHT)
        return DegradeLevel.L0_NORMAL
    }

    fun reset() {
        currentLevel = DegradeLevel.L0_NORMAL
        lastPollTs = -1.0
        lastLevelChangeTs = 0.0
    }
}
