package com.btb.ondevice.ui

/**
 * Station Dim (AGENTS.md section 3): the black screen that keeps the camera HAL awake. The dim must
 * survive an activity recreation (rotation, font change, the system reclaiming the activity), and the
 * window brightness it sets is lost with the window, so the activity writes [isOn] to its saved state and
 * calls [onRecreated] with it. The dim is only restored when the monitoring session is still running
 * ([onSessionConfirmed]); a session that ended meanwhile drops the saved flag ([onSessionLost]).
 *
 * Pure Kotlin so the rules are JVM-tested; the activity only applies the window change.
 */
class StationDimState {
    /** Whether the station screen is dimmed right now. */
    var isOn: Boolean = false
        private set

    /** A saved flag from before a recreation, waiting for the session to be confirmed. */
    private var restorePending: Boolean = false

    /** The user (or the restore) turned the dim on. Refused while there is no session to dim. */
    fun turnOn(monitoring: Boolean): Boolean {
        isOn = monitoring
        return isOn
    }

    fun turnOff() {
        isOn = false
    }

    /** The activity was recreated. [savedOn] is the flag it saved before going away. */
    fun onRecreated(savedOn: Boolean) {
        restorePending = savedOn
        isOn = false
    }

    /**
     * The service reports a live session. Returns true when the dim has just been restored, so the
     * activity should apply the window change. A pending restore is consumed here, once.
     */
    fun onSessionConfirmed(monitoring: Boolean): Boolean {
        if (!restorePending) return false
        restorePending = false
        if (!monitoring) return false
        isOn = true
        return true
    }

    /** No live session after a recreation: the saved dim is not applied to nothing. */
    fun onSessionLost() {
        restorePending = false
        isOn = false
    }
}
