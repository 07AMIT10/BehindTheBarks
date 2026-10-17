package com.btb.ondevice.net

import com.google.common.truth.Truth.assertThat
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import java.security.KeyPair
import java.security.KeyPairGenerator
import java.security.Signature
import java.util.Base64

/** A software P-256 key: the Keystore is unavailable in JVM unit tests (hence the Signer seam). */
class SoftwareSigner : Signer {
    private val kp: KeyPair = KeyPairGenerator.getInstance("EC").apply {
        initialize(256)
    }.generateKeyPair()

    val publicKey: java.security.PublicKey get() = kp.public

    override fun sign(data: ByteArray): ByteArray =
        Signature.getInstance("SHA256withECDSA").run { initSign(kp.private); update(data); sign() }

    override fun publicSpkiB64(): String =
        Base64.getEncoder().encodeToString(kp.public.encoded)
}

class PairingTest {

    private lateinit var server: MockWebServer

    @BeforeEach
    fun setUp() {
        server = MockWebServer()
        server.start()
    }

    @AfterEach
    fun tearDown() {
        try {
            server.shutdown()
        } catch (_: Throwable) {}
    }

    @Test
    fun `pair posts the code, the public key and the device name`() = runBlocking {
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse =
                MockResponse().setResponseCode(200)
                    .setBody("""{"station_id":"st1","household_id":"hh1","server_time":1700000000.0}""")
        }
        val result = StationPairing.pair(server.url("/").toString(), "the-code", "Pixel 8", SoftwareSigner(), true)
        assertThat(result).isInstanceOf(PairResult.Ok::class.java)
        val pairing = (result as PairResult.Ok).pairing
        assertThat(pairing.stationId).isEqualTo("st1")
        assertThat(pairing.householdId).isEqualTo("hh1")

        val body = Json.parseToJsonElement(server.takeRequest().body.readUtf8()).jsonObject
        assertThat(body["code"]?.jsonPrimitive?.content).isEqualTo("the-code")
        assertThat(body["device_name"]?.jsonPrimitive?.content).isEqualTo("Pixel 8")
        assertThat(body["pubkey_spki_b64"]?.jsonPrimitive?.content?.isNotBlank()).isTrue()
    }

    @Test
    fun `a 400 from the server is an invalid code`() = runBlocking {
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse =
                MockResponse().setResponseCode(400).setBody("""{"error":"invalid_or_expired_code"}""")
        }
        val result = StationPairing.pair(server.url("/").toString(), "used-code", "Pixel 8", SoftwareSigner(), true)
        assertThat(result).isEqualTo(PairResult.InvalidCode)
    }

    @Test
    fun `the auth envelope signs nonce, station id and device`() {
        val signer = SoftwareSigner()
        val nonceB64 = Base64.getEncoder().encodeToString(ByteArray(32) { 7 })
        val envelope = Json.parseToJsonElement(
            StationPairing.authEnvelope(nonceB64, "st1", "Pixel 8", signer)
        ).jsonObject
        assertThat(envelope["type"]?.jsonPrimitive?.content).isEqualTo("auth")

        // the server verifies exactly this: signature over the message, against the registered key
        val sig = Base64.getDecoder().decode(envelope["sig"]?.jsonPrimitive?.content)
        val ok = Signature.getInstance("SHA256withECDSA").run {
            initVerify(signer.publicKey)
            update(StationPairing.authMessage(nonceB64, "st1", "Pixel 8"))
            verify(sig)
        }
        assertThat(ok).isTrue()
        // a different station id must not verify against the same signature
        val wrong = Signature.getInstance("SHA256withECDSA").run {
            initVerify(signer.publicKey)
            update(StationPairing.authMessage(nonceB64, "other", "Pixel 8"))
            verify(sig)
        }
        assertThat(wrong).isFalse()
    }

    @Test
    fun `the QR pair_url is used as the endpoint and is never doubled`() {
        assertThat(StationPairing.pairEndpoint("http://192.168.1.10:8000/pair")).isEqualTo("http://192.168.1.10:8000/pair")
        assertThat(StationPairing.pairEndpoint("http://192.168.1.10:8000/pair/")).isEqualTo("http://192.168.1.10:8000/pair")
        assertThat(StationPairing.pairEndpoint("https://host.example")).isEqualTo("https://host.example/pair")
        assertThat(StationPairing.pairEndpoint("https://host.example/")).isEqualTo("https://host.example/pair")
    }

    @Test
    fun `pair refuses a cleartext pairing endpoint in release without sending a request`() = runBlocking {
        val result = StationPairing.pair(server.url("/").toString(), "the-code", "Pixel 8", SoftwareSigner(), false)
        assertThat(result).isInstanceOf(PairResult.Refused::class.java)
        assertThat(server.requestCount).isEqualTo(0)
    }
}
