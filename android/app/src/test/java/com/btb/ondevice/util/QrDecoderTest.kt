package com.btb.ondevice.util

import com.google.common.truth.Truth.assertThat
import org.junit.jupiter.api.Test

class QrDecoderTest {

    @Test
    fun `parsePairingText handles JSON payload with token and device`() {
        val json = """{"v":1,"url":"https://api.btb.local","token":"secret-token-123","device":"Kitchen-Cam"}"""
        val config = QrDecoder.parsePairingText(json)
        assertThat(config).isNotNull()
        assertThat(config?.url).isEqualTo("wss://api.btb.local/ingest-events")
        assertThat(config?.token).isEqualTo("secret-token-123")
        assertThat(config?.deviceName).isEqualTo("Kitchen-Cam")
    }

    @Test
    fun `parsePairingText normalizes direct HTTP URL to WSS and appends ingest-events`() {
        val raw = "https://mypet.trycloudflare.com"
        val config = QrDecoder.parsePairingText(raw)
        assertThat(config).isNotNull()
        assertThat(config?.url).isEqualTo("wss://mypet.trycloudflare.com/ingest-events")
        assertThat(config?.token).isNull()
    }

    @Test
    fun `parsePairingText preserves existing ingest-events path`() {
        val raw = "ws://192.168.1.100:8000/ingest-events"
        val config = QrDecoder.parsePairingText(raw)
        assertThat(config).isNotNull()
        assertThat(config?.url).isEqualTo("ws://192.168.1.100:8000/ingest-events")
    }

    @Test
    fun `parsePairingText rejects empty or invalid text`() {
        assertThat(QrDecoder.parsePairingText("")).isNull()
        assertThat(QrDecoder.parsePairingText("hello world")).isNull()
        assertThat(QrDecoder.parsePairingText("{not a valid json}")).isNull()
    }
}
