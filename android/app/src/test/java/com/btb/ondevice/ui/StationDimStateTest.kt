package com.btb.ondevice.ui

import com.google.common.truth.Truth.assertThat
import org.junit.jupiter.api.Test

/** Station Dim survives an activity recreation (rotation, font change) only while the session runs. */
class StationDimStateTest {

    @Test
    fun `the dim is refused while there is no monitoring session to dim`() {
        val dim = StationDimState()
        assertThat(dim.turnOn(monitoring = false)).isFalse()
        assertThat(dim.isOn).isFalse()
    }

    @Test
    fun `the dim turns on and off while monitoring`() {
        val dim = StationDimState()
        assertThat(dim.turnOn(monitoring = true)).isTrue()
        assertThat(dim.isOn).isTrue()
        dim.turnOff()
        assertThat(dim.isOn).isFalse()
    }

    @Test
    fun `a recreated screen restores a dim that was on when the session is still running`() {
        val before = StationDimState()
        before.turnOn(monitoring = true)
        val saved = before.isOn // what onSaveInstanceState writes

        val after = StationDimState()          // the new activity instance
        after.onRecreated(savedOn = saved)
        assertThat(after.isOn).isFalse()       // nothing is applied until the session is confirmed
        assertThat(after.onSessionConfirmed(monitoring = true)).isTrue()
        assertThat(after.isOn).isTrue()
    }

    @Test
    fun `the saved dim is consumed once so a later confirmation does not re-apply it`() {
        val after = StationDimState()
        after.onRecreated(savedOn = true)
        assertThat(after.onSessionConfirmed(monitoring = true)).isTrue()
        after.turnOff()                        // the person woke the screen
        assertThat(after.onSessionConfirmed(monitoring = true)).isFalse()
        assertThat(after.isOn).isFalse()
    }

    @Test
    fun `a session that ended while the screen was recreated drops the saved dim`() {
        val after = StationDimState()
        after.onRecreated(savedOn = true)
        after.onSessionLost()
        assertThat(after.onSessionConfirmed(monitoring = true)).isFalse()
        assertThat(after.isOn).isFalse()
    }

    @Test
    fun `no saved dim means nothing is restored`() {
        val after = StationDimState()
        after.onRecreated(savedOn = false)
        assertThat(after.onSessionConfirmed(monitoring = true)).isFalse()
        assertThat(after.isOn).isFalse()
    }
}
