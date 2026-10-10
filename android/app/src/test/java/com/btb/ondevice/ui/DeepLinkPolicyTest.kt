package com.btb.ondevice.ui

import com.google.common.truth.Truth.assertThat
import org.junit.jupiter.api.Test

class DeepLinkPolicyTest {
    @Test
    fun `token applies only to the already configured host`() {
        assertThat(DeepLinkPolicy.mayApplyToken("https://dash.example.com/ingest-events", "https://dash.example.com/ingest-events"))
            .isTrue()
        assertThat(DeepLinkPolicy.mayApplyToken("https://dash.example.com/ingest-events", "https://evil.test/ingest-events"))
            .isFalse()
    }

    @Test
    fun `no configured server means no token is applied`() {
        assertThat(DeepLinkPolicy.mayApplyToken("", "https://evil.test/ingest-events")).isFalse()
        assertThat(DeepLinkPolicy.mayApplyToken(null, "https://evil.test/ingest-events")).isFalse()
    }

    @Test
    fun `host comparison ignores case and garbage input never matches`() {
        assertThat(DeepLinkPolicy.mayApplyToken("https://Dash.Example.com/x", "https://dash.example.com/y")).isTrue()
        assertThat(DeepLinkPolicy.hostOf("not a url at all ::")).isNull()
    }
}
