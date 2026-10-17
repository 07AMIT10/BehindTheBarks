package com.btb.ondevice.ui

/**
 * The station's persisted configuration (T2.5). Shared by MainActivity and ScannerActivity so a
 * pairing done in the scanner is immediately visible to the monitoring session.
 */
object StationPrefs {
    const val NAME = "btb_station_prefs"
    const val URL = "server_url"
    const val DEVICE = "device_name"
    const val TOKEN = "access_token"
    const val STATION_ID = "station_id"
    const val HOUSEHOLD_ID = "household_id"
    const val PAIR_URL = "pair_url"
}
