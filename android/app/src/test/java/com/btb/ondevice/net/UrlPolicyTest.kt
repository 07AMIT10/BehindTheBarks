package com.btb.ondevice.net

import com.google.common.truth.Truth.assertThat
import org.junit.jupiter.api.Test

class UrlPolicyTest {

    private val refusedForInsecure = "Insecure server link (ws://). This build only connects over wss://."

    private fun assertAllowed(url: String, allowCleartext: Boolean) {
        assertThat(UrlPolicy.check(url, allowCleartext)).isEqualTo(UrlCheck.Allowed)
    }

    private fun assertRejected(url: String, allowCleartext: Boolean): String {
        val check = UrlPolicy.check(url, allowCleartext)
        assertThat(check).isInstanceOf(UrlCheck.Rejected::class.java)
        return (check as UrlCheck.Rejected).reason
    }

    @Test
    fun `release mode accepts only wss and https with a host, any case`() {
        assertAllowed("wss://example.test/ingest-events", allowCleartext = false)
        assertAllowed("https://example.test", allowCleartext = false)
        assertAllowed("WSS://Example.test/ingest-events", allowCleartext = false)
        assertAllowed("HTTPS://example.test", allowCleartext = false)
        assertAllowed("  wss://example.test/ingest-events  ", allowCleartext = false)
    }

    @Test
    fun `release mode refuses ws and http with the user-facing message`() {
        assertThat(assertRejected("ws://10.0.2.2:8000/ingest-events", allowCleartext = false)).isEqualTo(refusedForInsecure)
        assertThat(assertRejected("WS://10.0.2.2:8000/ingest-events", allowCleartext = false)).isEqualTo(refusedForInsecure)
        assertThat(assertRejected("http://example.test:8000", allowCleartext = false))
            .isEqualTo("Insecure server link (http://). This build only connects over wss://.")
    }

    @Test
    fun `other schemes, empty text and links without a host are rejected in release`() {
        assertRejected("ftp://example.test", allowCleartext = false)
        assertRejected("", allowCleartext = false)
        assertRejected("   ", allowCleartext = false)
        assertRejected("example.test/ingest-events", allowCleartext = false)
        assertRejected("wss://", allowCleartext = false)
        assertRejected("wss:///ingest-events", allowCleartext = false)
        assertRejected("https://", allowCleartext = false)
    }

    @Test
    fun `the real host decides, not text inside the userinfo or path`() {
        // Secure scheme, real host evil.test: judged as a wss link to evil.test (allowed as a scheme; the host is what
        // the socket connects to, so the host check below is the one a reviewer must read).
        assertThat(UrlPolicy.hostOf("wss://good.com@evil.test/ingest-events")).isEqualTo("evil.test")
        assertAllowed("wss://good.com@evil.test/ingest-events", allowCleartext = false)
        // The scheme is read from the start of the link: a secure-looking host cannot launder a ws:// link.
        assertRejected("ws://wss.good.com@evil.test/ingest-events", allowCleartext = false)
        assertRejected("ws://wss://good.com/ingest-events", allowCleartext = false)
        assertRejected("http://https.good.com@evil.test", allowCleartext = false)
        // Characters that make java.net.URI fail are rejected, not guessed at.
        assertRejected("wss://good.com\\@evil.test/ingest-events", allowCleartext = false)
        assertRejected("wss://good.com /ingest-events", allowCleartext = false)
    }

    @Test
    fun `debug mode also accepts ws and http, still refuses other schemes and hostless links`() {
        assertAllowed("ws://10.0.2.2:8000/ingest-events", allowCleartext = true)
        assertAllowed("ws://127.0.0.1:8000/ingest-events", allowCleartext = true)
        assertAllowed("http://192.168.1.50:8000", allowCleartext = true)
        assertAllowed("wss://example.test/ingest-events", allowCleartext = true)
        assertAllowed("https://example.test", allowCleartext = true)
        assertRejected("ftp://example.test", allowCleartext = true)
        assertRejected("", allowCleartext = true)
        assertRejected("ws://", allowCleartext = true)
    }

    @Test
    fun `hostOf is null for garbage and lowercases the host`() {
        assertThat(UrlPolicy.hostOf("not a url at all ::")).isNull()
        assertThat(UrlPolicy.hostOf("wss://Dash.Example.com/x")).isEqualTo("dash.example.com")
    }
}
