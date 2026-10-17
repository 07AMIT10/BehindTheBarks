package com.btb.ondevice.ui

import com.google.common.truth.Truth.assertThat
import org.junit.jupiter.api.Test

class ScanRouteTest {
    @Test
    fun `while monitoring the running service scans`() {
        assertThat(ScanRoute.choose(monitoring = true)).isEqualTo(ScanRoute.Target.MONITOR_SERVICE)
    }

    @Test
    fun `without monitoring the standalone scanner pairs the station`() {
        assertThat(ScanRoute.choose(monitoring = false)).isEqualTo(ScanRoute.Target.STANDALONE_SCANNER)
    }
}
