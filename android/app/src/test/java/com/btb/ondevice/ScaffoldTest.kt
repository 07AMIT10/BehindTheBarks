package com.btb.ondevice

import com.google.common.truth.Truth.assertThat
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.Serializable
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import okhttp3.Request
import org.junit.jupiter.api.Test

class ScaffoldTest {
    @Test
    fun `app module uses the on-device application id`() {
        assertThat(BuildConfig.APPLICATION_ID).isEqualTo("com.btb.ondevice")
    }

    @Test
    fun `envelope skeleton round-trips with explicit nulls`() {
        val json = Json { explicitNulls = true; encodeDefaults = true }
        val envelope = Envelope("frame", FrameData(ts = 1.5, bbox = null))

        val encoded = json.encodeToString(envelope)

        assertThat(encoded).isEqualTo("""{"type":"frame","data":{"ts":1.5,"bbox":null}}""")
        assertThat(json.decodeFromString<Envelope>(encoded)).isEqualTo(envelope)
    }

    @Test
    fun `okhttp can address the ingest-events endpoint`() {
        val request = Request.Builder().url("http://10.0.2.2:8000/ingest-events").build()

        assertThat(request.url.encodedPath).isEqualTo("/ingest-events")
        assertThat(request.method).isEqualTo("GET")
    }

    @Test
    fun `coroutines-test runs`() = runTest {
        assertThat(1).isEqualTo(1)
    }
}

@Serializable
private data class Envelope(val type: String, val data: FrameData)

@Serializable
private data class FrameData(val ts: Double, val bbox: List<Double>?)