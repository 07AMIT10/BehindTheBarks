package com.btb.ondevice.util

import com.google.common.truth.Truth.assertThat
import org.junit.jupiter.api.Test

class QrDecoderTest {

    @Test
    fun `parsePairingText handles v1 JSON payload with token and device`() {
        val json = """{"v":1,"url":"https://api.btb.local","token":"secret-token-123","device":"Kitchen-Cam"}"""
        val config = QrDecoder.parsePairingText(json) as PairingTarget.Manual
        assertThat(config.config.url).isEqualTo("wss://api.btb.local/ingest-events")
        assertThat(config.config.token).isEqualTo("secret-token-123")
        assertThat(config.config.deviceName).isEqualTo("Kitchen-Cam")
    }

    @Test
    fun `parsePairingText handles v2 JSON pairing code`() {
        val json = """{"v":2,"url":"wss://host/ingest-events","pair_url":"https://host","code":"abc123"}"""
        val code = QrDecoder.parsePairingText(json) as PairingTarget.Code
        assertThat(code.wsUrl).isEqualTo("wss://host/ingest-events")
        assertThat(code.pairUrl).isEqualTo("https://host")
        assertThat(code.code).isEqualTo("abc123")
    }

    @Test
    fun `v2 without a code is rejected`() {
        assertThat(QrDecoder.parsePairingText("""{"v":2,"url":"wss://h/ingest-events","pair_url":"https://h"}""")).isNull()
    }

    @Test
    fun `parsePairingText normalizes direct HTTP URL to WSS and appends ingest-events`() {
        val config = (QrDecoder.parsePairingText("https://mypet.trycloudflare.com") as PairingTarget.Manual).config
        assertThat(config.url).isEqualTo("wss://mypet.trycloudflare.com/ingest-events")
        assertThat(config.token).isNull()
    }

    @Test
    fun `parsePairingText preserves existing ingest-events path`() {
        val config = (QrDecoder.parsePairingText("ws://192.168.1.100:8000/ingest-events") as PairingTarget.Manual).config
        assertThat(config.url).isEqualTo("ws://192.168.1.100:8000/ingest-events")
    }

    @Test
    fun `parsePairingText rejects empty or invalid text`() {
        assertThat(QrDecoder.parsePairingText("")).isNull()
        assertThat(QrDecoder.parsePairingText("hello world")).isNull()
        assertThat(QrDecoder.parsePairingText("{not a valid json}")).isNull()
    }
}
