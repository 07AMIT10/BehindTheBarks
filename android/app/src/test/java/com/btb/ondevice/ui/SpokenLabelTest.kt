package com.btb.ondevice.ui

import com.google.common.truth.Truth.assertThat
import org.junit.jupiter.api.Test

class SpokenLabelTest {
    @Test
    fun `emoji prefixes are dropped`() {
        assertThat(spokenLabel("🛡️ Privacy ON")).isEqualTo("Privacy ON")
        assertThat(spokenLabel("🌙 Station Dim")).isEqualTo("Station Dim")
        assertThat(spokenLabel("👁️ 1 viewer watching")).isEqualTo("1 viewer watching")
    }

    @Test
    fun `status symbols are dropped and words kept`() {
        assertThat(spokenLabel("● Live · Connected to Server")).isEqualTo("Live · Connected to Server")
        assertThat(spokenLabel("▲ Hide HUD")).isEqualTo("Hide HUD")
    }

    @Test
    fun `plain text is unchanged`() {
        assertThat(spokenLabel("Disconnect")).isEqualTo("Disconnect")
        assertThat(spokenLabel("USB (Local)")).isEqualTo("USB (Local)")
    }
}
