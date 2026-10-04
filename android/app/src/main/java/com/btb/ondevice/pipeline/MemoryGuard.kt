package com.btb.ondevice.pipeline

import android.app.ActivityManager
import android.content.Context
import android.os.Debug
import com.btb.ondevice.ml.ModelRegistry

interface MemoryProvider {
    fun isLowMemory(): Boolean
    fun getPssKb(): Long
}

class AndroidMemoryProvider(private val context: Context) : MemoryProvider {
    private val activityManager by lazy { context.getSystemService(Context.ACTIVITY_SERVICE) as? ActivityManager }

    override fun isLowMemory(): Boolean {
        val memInfo = ActivityManager.MemoryInfo()
        activityManager?.getMemoryInfo(memInfo) ?: return false
        return memInfo.lowMemory
    }

    override fun getPssKb(): Long {
        return Debug.getPss()
    }
}

class MemoryGuard(
    private val memoryProvider: MemoryProvider,
    private val modelRegistry: ModelRegistry,
    val maxPssKb: Long = 400 * 1024L, // 400 MB whole-app budget (getPss measures the app, not just models)
    val pollIntervalS: Double = 30.0,
    private val clock: () -> Double = { System.currentTimeMillis() / 1000.0 },
) {
    private var lastPollTs = -1.0
    var lastTrimReason: String? = null
        private set

    fun checkMemory(force: Boolean = false): Boolean {
        val now = clock()
        if (!force && lastPollTs >= 0 && (now - lastPollTs) < pollIntervalS) {
            return false
        }
        lastPollTs = now

        val isLow = memoryProvider.isLowMemory()
        val pss = memoryProvider.getPssKb()

        if (isLow || pss > maxPssKb) {
            lastTrimReason = if (isLow) "system_low_memory" else "pss_budget_exceeded ($pss KB > $maxPssKb KB)"
            modelRegistry.trimMemory(explicitMemoryGuard = true)
            return true
        }
        return false
    }

    fun onTrimMemory(level: Int) {
        if (level >= 15) { // TRIM_MEMORY_RUNNING_CRITICAL (15) or higher
            lastTrimReason = "trim_memory_signal ($level)"
            modelRegistry.trimMemory(explicitMemoryGuard = true, trimLevel = level)
        }
    }
}
