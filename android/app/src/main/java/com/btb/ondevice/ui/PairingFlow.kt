package com.btb.ondevice.ui

import android.content.SharedPreferences
import com.btb.ondevice.net.PairResult
import com.btb.ondevice.net.Signer
import com.btb.ondevice.net.StationPairing
import com.btb.ondevice.util.PairingTarget

/**
 * The one place a v2 pairing code becomes a saved station identity (T2.5). The dashboard QR scan, the
 * Pair by QR screen, the clipboard paste and the debug intent extra all end here, so a pairing made by
 * any of them is stored the same way.
 */
object PairingFlow {
    suspend fun pairAndSave(
        prefs: SharedPreferences,
        target: PairingTarget.Code,
        deviceName: String,
        signer: Signer,
    ): PairResult {
        val result = StationPairing.pair(target.pairUrl, target.code, deviceName, signer)
        if (result is PairResult.Ok) {
            prefs.edit()
                .putString(StationPrefs.STATION_ID, result.pairing.stationId)
                .putString(StationPrefs.HOUSEHOLD_ID, result.pairing.householdId)
                .putString(StationPrefs.PAIR_URL, target.pairUrl)
                .putString(StationPrefs.URL, target.wsUrl)
                .apply()
        }
        return result
    }
}
