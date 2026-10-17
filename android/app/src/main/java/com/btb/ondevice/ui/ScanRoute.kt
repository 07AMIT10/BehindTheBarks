package com.btb.ondevice.ui

/**
 * Where the main screen's "Scan QR" button goes. While monitoring, the running service's camera is
 * already a scanner (it reads the dashboard code in place). Otherwise the standalone ScannerActivity
 * pairs the station without starting monitoring. Kept free of Android types for a JVM test.
 */
internal object ScanRoute {
    enum class Target { MONITOR_SERVICE, STANDALONE_SCANNER }

    fun choose(monitoring: Boolean): Target =
        if (monitoring) Target.MONITOR_SERVICE else Target.STANDALONE_SCANNER
}
