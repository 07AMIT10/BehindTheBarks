package com.btb.ondevice.capture

import com.google.common.truth.Truth.assertThat
import org.junit.jupiter.api.Test

/** C1: the station id a monitoring session starts with, intent first, persisted pairing second. */
class StationIdentityTest {

    @Test
    fun `an explicit intent extra wins`() {
        assertThat(StationIdentity.resolveStationId("st_intent", "st_saved")).isEqualTo("st_intent")
    }

    @Test
    fun `a saved pairing is used when the intent carries no station id`() {
        assertThat(StationIdentity.resolveStationId(null, "st_saved")).isEqualTo("st_saved")
        assertThat(StationIdentity.resolveStationId("", "st_saved")).isEqualTo("st_saved")
        assertThat(StationIdentity.resolveStationId("   ", "st_saved")).isEqualTo("st_saved")
    }

    @Test
    fun `blank and null fall through to null`() {
        assertThat(StationIdentity.resolveStationId(null, null)).isNull()
        assertThat(StationIdentity.resolveStationId("", "  ")).isNull()
        assertThat(StationIdentity.resolveStationId("  ", "")).isNull()
    }

    @Test
    fun `whitespace is trimmed around the chosen value`() {
        assertThat(StationIdentity.resolveStationId("  st_x  ", "st_saved")).isEqualTo("st_x")
        assertThat(StationIdentity.resolveStationId(null, " st_y ")).isEqualTo("st_y")
    }
}
