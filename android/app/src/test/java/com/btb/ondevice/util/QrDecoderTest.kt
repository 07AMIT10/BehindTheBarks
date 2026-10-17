package com.btb.ondevice.util

import com.google.common.truth.Truth.assertThat
import org.junit.jupiter.api.Test

class QrDecoderTest {

    @Test
    fun `parsePairingText handles v1 JSON payload with token and device`() {
        val json = """{"v":1,"url":"https://api.btb.local","token":"secret-token-123","device":"Kitchen-Cam"}"""
        val config = QrDecoder.parsePairingText(json, true) as PairingTarget.Manual
        assertThat(config.config.url).isEqualTo("wss://api.btb.local/ingest-events")
        assertThat(config.config.token).isEqualTo("secret-token-123")
        assertThat(config.config.deviceName).isEqualTo("Kitchen-Cam")
    }

    @Test
    fun `parsePairingText handles v2 JSON pairing code`() {
        val json = """{"v":2,"url":"wss://host/ingest-events","pair_url":"https://host","code":"abc123"}"""
        val code = QrDecoder.parsePairingText(json, true) as PairingTarget.Code
        assertThat(code.wsUrl).isEqualTo("wss://host/ingest-events")
        assertThat(code.pairUrl).isEqualTo("https://host")
        assertThat(code.code).isEqualTo("abc123")
    }

    @Test
    fun `v2 without a code is rejected`() {
        assertThat(QrDecoder.parsePairingText("""{"v":2,"url":"wss://h/ingest-events","pair_url":"https://h"}""", true)).isNull()
    }

    @Test
    fun `parsePairingText normalizes direct HTTP URL to WSS and appends ingest-events`() {
        val config = (QrDecoder.parsePairingText("https://mypet.trycloudflare.com", true) as PairingTarget.Manual).config
        assertThat(config.url).isEqualTo("wss://mypet.trycloudflare.com/ingest-events")
        assertThat(config.token).isNull()
    }

    @Test
    fun `parsePairingText preserves existing ingest-events path`() {
        val config = (QrDecoder.parsePairingText("ws://192.168.1.100:8000/ingest-events", true) as PairingTarget.Manual).config
        assertThat(config.url).isEqualTo("ws://192.168.1.100:8000/ingest-events")
    }

    @Test
    fun `parsePairingText rejects empty or invalid text`() {
        assertThat(QrDecoder.parsePairingText("", true)).isNull()
        assertThat(QrDecoder.parsePairingText("hello world", true)).isNull()
        assertThat(QrDecoder.parsePairingText("{not a valid json}", true)).isNull()
    }

    @Test
    fun `release parse path refuses ws and http links (direct, v1 and v2 payloads)`() {
        val refused = "Insecure server link (ws://). This build only connects over wss://."
        assertThat(QrDecoder.parsePairingText("ws://10.0.2.2:8000/ingest-events", false))
            .isEqualTo(PairingTarget.Rejected(refused))
        assertThat(QrDecoder.parsePairingText("http://192.168.1.50:8000", false))
            .isEqualTo(PairingTarget.Rejected("Insecure server link (http://). This build only connects over wss://."))
        assertThat(QrDecoder.parsePairingText("""{"v":1,"url":"ws://10.0.2.2:8000","token":"t"}""", false))
            .isEqualTo(PairingTarget.Rejected(refused))
        assertThat(QrDecoder.parsePairingText("""{"v":2,"url":"ws://10.0.2.2:8000/ingest-events","pair_url":"https://h","code":"c"}""", false))
            .isEqualTo(PairingTarget.Rejected(refused))
        // A v2 code whose pairing endpoint is plain http is refused even when the websocket URL is wss.
        assertThat(QrDecoder.parsePairingText("""{"v":2,"url":"wss://h/ingest-events","pair_url":"http://h:8000","code":"c"}""", false))
            .isEqualTo(PairingTarget.Rejected("Insecure server link (http://). This build only connects over wss://."))
    }

    @Test
    fun `release parse path still accepts wss and https payloads`() {
        val config = (QrDecoder.parsePairingText("https://mypet.trycloudflare.com", false) as PairingTarget.Manual).config
        assertThat(config.url).isEqualTo("wss://mypet.trycloudflare.com/ingest-events")
        val code = QrDecoder.parsePairingText("""{"v":2,"url":"wss://h/ingest-events","pair_url":"https://h","code":"c"}""", false)
        assertThat(code).isInstanceOf(PairingTarget.Code::class.java)
    }

    @Test
    fun `debug parse path keeps ws links`() {
        val config = (QrDecoder.parsePairingText("ws://10.0.2.2:8000/ingest-events", true) as PairingTarget.Manual).config
        assertThat(config.url).isEqualTo("ws://10.0.2.2:8000/ingest-events")
    }
}
