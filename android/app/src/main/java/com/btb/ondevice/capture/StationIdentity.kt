package com.btb.ondevice.capture

/**
 * C1: which station identity a monitoring session identifies itself with.
 *
 * Pure so the fallback rule is unit-tested without Android or SharedPreferences.
 */
object StationIdentity {

    /**
     * The station id for the hello: an explicit intent extra always wins (a normal start pushes
     * it, `MainActivity.startMonitoringService`), otherwise the pairing persisted in prefs is used.
     *
     * The persisted fallback matters because the service returns START_STICKY: after the process
     * dies the system redelivers `onStartCommand` with a null intent, and every other start input
     * (server_url, device_name, access_token) already falls back to that same prefs file. Without
     * a station id in the hello the server refuses the handshake (4401), `onPairingInvalid` fires
     * and the UI deletes the saved pairing — monitoring of a household station would stop untended
     * by nothing more than an OS process kill.
     */
    fun resolveStationId(fromIntent: String?, persisted: String?): String? =
        fromIntent?.trim()?.takeIf { it.isNotBlank() }
            ?: persisted?.trim()?.takeIf { it.isNotBlank() }
}
