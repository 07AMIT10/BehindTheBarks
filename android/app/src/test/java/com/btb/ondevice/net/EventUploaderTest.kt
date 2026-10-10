package com.btb.ondevice.net

import com.btb.ondevice.contracts.AudioEvent
import com.btb.ondevice.contracts.AudioLabel
import com.btb.ondevice.contracts.FrameEvent
import com.btb.ondevice.pipeline.StubPerception
import com.google.common.truth.Truth.assertThat
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.delay
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okio.ByteString
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import java.io.File
import java.nio.file.Files

class EventUploaderTest {

    private lateinit var server: MockWebServer
    private lateinit var tempDir: File
    private lateinit var spool: EventSpool

    @BeforeEach
    fun setUp() {
        server = MockWebServer()
        server.start()
        tempDir = Files.createTempDirectory("uploader_test").toFile()
        spool = EventSpool(tempDir)
    }

    @AfterEach
    fun tearDown() {
        try {
            server.shutdown()
        } catch (_: Throwable) {}
        tempDir.deleteRecursively()
    }

    @Test
    fun `handshake sends hello and handles hello_ack to become connected`() = runBlocking {
        val helloReceived = CompletableDeferred<String>()
        val connectedDeferred = CompletableDeferred<Unit>()

        server.enqueue(MockResponse().withWebSocketUpgrade(object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) {
                if (text.contains(""""type":"hello"""")) {
                    helloReceived.complete(text)
                    webSocket.send("""{"type":"hello_ack","server_time":1700000000.0}""")
                }
            }
        }))

        val uploader = EventUploader(
            serverUrl = server.url("/ingest-events").toString(),
            deviceName = "test-galaxy",
            spool = spool,
            listener = object : EventUploaderListener {
                override fun onStateChanged(state: ConnectionState) {
                    if (state == ConnectionState.CONNECTED) {
                        connectedDeferred.complete(Unit)
                    }
                }
            }
        )

        uploader.start()

        val helloText = withTimeout(5000) { helloReceived.await() }
        assertThat(helloText).contains(""""type":"hello"""")
        assertThat(helloText).contains(""""proto":1""")
        assertThat(helloText).contains(""""device":"test-galaxy"""")

        withTimeout(5000) { connectedDeferred.await() }
        assertThat(uploader.connectionState).isEqualTo(ConnectionState.CONNECTED)

        uploader.stop()
    }

    @Test
    fun `uploader sends frame audio rules and binary preview`() = runBlocking {
        val messagesReceived = mutableListOf<String>()
        val binariesReceived = mutableListOf<ByteString>()
        val doneSignal = CompletableDeferred<Unit>()

        server.enqueue(MockResponse().withWebSocketUpgrade(object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) {
                if (text.contains(""""type":"hello"""")) {
                    webSocket.send("""{"type":"hello_ack","server_time":1700000000.0}""")
                } else {
                    messagesReceived.add(text)
                    if (messagesReceived.size >= 3 && binariesReceived.size >= 1) {
                        doneSignal.complete(Unit)
                    }
                }
            }

            override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
                binariesReceived.add(bytes)
                if (messagesReceived.size >= 3 && binariesReceived.size >= 1) {
                    doneSignal.complete(Unit)
                }
            }
        }))

        val connectedDeferred = CompletableDeferred<Unit>()
        val uploader = EventUploader(
            serverUrl = server.url("/ingest-events").toString(),
            deviceName = "test-galaxy",
            spool = spool,
            listener = object : EventUploaderListener {
                override fun onStateChanged(state: ConnectionState) {
                    if (state == ConnectionState.CONNECTED) {
                        connectedDeferred.complete(Unit)
                    }
                }
            }
        )

        uploader.start()
        withTimeout(5000) { connectedDeferred.await() }

        val stub = StubPerception()
        val step = stub.step()

        val frameSent = uploader.sendFrame(step.frame)
        val audioSent = uploader.sendAudio(step.audio ?: AudioEvent(ts = 1.0, label = AudioLabel.bark, score = 0.9))
        val rulesSent = uploader.sendRules(step.rules)
        val previewSent = uploader.sendPreview(step.previewJpeg!!)

        assertThat(frameSent).isTrue()
        assertThat(audioSent).isTrue()
        assertThat(rulesSent).isTrue()
        assertThat(previewSent).isTrue()

        withTimeout(5000) { doneSignal.await() }

        assertThat(messagesReceived.any { it.contains(""""type":"frame"""") }).isTrue()
        assertThat(messagesReceived.any { it.contains(""""type":"audio"""") }).isTrue()
        assertThat(messagesReceived.any { it.contains(""""type":"rules"""") }).isTrue()

        assertThat(binariesReceived.size).isEqualTo(1)
        assertThat(binariesReceived[0].get(0)).isEqualTo(EventUploader.KIND_PREVIEW)

        uploader.stop()
    }

    @Test
    fun `disconnected events are spooled and flushed on reconnect`() = runBlocking {
        val flushedSignal = CompletableDeferred<Unit>()
        val messagesReceived = mutableListOf<String>()

        server.enqueue(MockResponse().withWebSocketUpgrade(object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) {
                if (text.contains(""""type":"hello"""")) {
                    webSocket.send("""{"type":"hello_ack","server_time":1700000000.0}""")
                } else {
                    messagesReceived.add(text)
                    if (messagesReceived.size >= 2) {
                        flushedSignal.complete(Unit)
                    }
                }
            }
        }))

        val uploader = EventUploader(
            serverUrl = server.url("/ingest-events").toString(),
            deviceName = "test-galaxy",
            spool = spool,
        )

        // While disconnected: send audio, rules, and frame
        val now = uploader.clock()
        val audioSent = uploader.sendAudio(AudioEvent(ts = now, label = AudioLabel.growl, score = 0.9))
        val rulesSent = uploader.sendRules(StubPerception(clock = { now }).step().rules.copy(ts = now + 0.1))
        val frameSent = uploader.sendFrame(StubPerception(clock = { now }).step().frame)

        // Audio and rules are spooled; frame is dropped
        assertThat(audioSent).isTrue()
        assertThat(rulesSent).isTrue()
        assertThat(frameSent).isFalse()
        assertThat(spool.sizeBytes).isGreaterThan(0L)

        // Now start connection
        uploader.start()

        withTimeout(5000) { flushedSignal.await() }

        assertThat(messagesReceived.any { it.contains(""""type":"audio"""") }).isTrue()
        assertThat(messagesReceived.any { it.contains(""""type":"rules"""") }).isTrue()
        assertThat(spool.sizeBytes).isEqualTo(0L)

        uploader.stop()
    }

    @Test
    fun `terminal close code terminates uploader without reconnect`() = runBlocking {
        val terminatedSignal = CompletableDeferred<Unit>()

        server.enqueue(MockResponse().withWebSocketUpgrade(object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) {
                if (text.contains(""""type":"hello"""")) {
                    // Close with 4409 (Busy)
                    webSocket.close(EventUploader.CLOSE_BUSY, "Another phone is already streaming")
                }
            }
        }))

        val uploader = EventUploader(
            serverUrl = server.url("/ingest-events").toString(),
            deviceName = "test-galaxy",
            spool = spool,
            listener = object : EventUploaderListener {
                override fun onStateChanged(state: ConnectionState) {
                    if (state == ConnectionState.TERMINATED) {
                        terminatedSignal.complete(Unit)
                    }
                }
            }
        )

        uploader.start()

        withTimeout(5000) { terminatedSignal.await() }
        assertThat(uploader.connectionState).isEqualTo(ConnectionState.TERMINATED)
    }

    @Test
    fun `stop sends bye then a normal close frame`() = runBlocking {
        val byeReceived = CompletableDeferred<String>()
        val closingReceived = CompletableDeferred<Int>()
        val connectedDeferred = CompletableDeferred<Unit>()

        server.enqueue(MockResponse().withWebSocketUpgrade(object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) {
                if (text.contains(""""type":"hello"""")) {
                    webSocket.send("""{"type":"hello_ack","server_time":1700000000.0}""")
                } else if (text.contains(""""type":"bye"""")) {
                    byeReceived.complete(text)
                }
            }

            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                if (code == EventUploader.CLOSE_NORMAL) {
                    closingReceived.complete(code)
                }
            }
        }))

        val uploader = EventUploader(
            serverUrl = server.url("/ingest-events").toString(),
            deviceName = "test-galaxy",
            spool = spool,
            listener = object : EventUploaderListener {
                override fun onStateChanged(state: ConnectionState) {
                    if (state == ConnectionState.CONNECTED) {
                        connectedDeferred.complete(Unit)
                    }
                }
            }
        )

        uploader.start()
        withTimeout(5000) { connectedDeferred.await() }
        uploader.stop()

        withTimeout(3000) {
            val bye = byeReceived.await()
            assertThat(bye).contains(""""type":"bye"""")
            assertThat(closingReceived.await()).isEqualTo(EventUploader.CLOSE_NORMAL)
        }
        assertThat(uploader.connectionState).isEqualTo(ConnectionState.TERMINATED)
    }

    @Test
    fun `device name with quotes still produces valid hello json`() = runBlocking {
        val helloReceived = CompletableDeferred<String>()

        server.enqueue(MockResponse().withWebSocketUpgrade(object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) {
                if (text.contains(""""type":"hello"""")) {
                    helloReceived.complete(text)
                    webSocket.send("""{"type":"hello_ack","server_time":1700000000.0}""")
                }
            }
        }))

        val rawName = "Dog \"Cam\"\\1"
        val uploader = EventUploader(
            serverUrl = server.url("/ingest-events").toString(),
            deviceName = rawName,
            spool = spool,
        )

        uploader.start()

        val helloText = withTimeout(5000) { helloReceived.await() }
        val hello = Json.parseToJsonElement(helloText).jsonObject
        assertThat(hello["device"]?.jsonPrimitive?.content).isEqualTo(rawName)

        uploader.stop()
    }

    @Test
    fun `late failure after stop does not resurrect the uploader`() = runBlocking {
        val connectedDeferred = CompletableDeferred<Unit>()

        server.enqueue(MockResponse().withWebSocketUpgrade(object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) {
                if (text.contains(""""type":"hello"""")) {
                    webSocket.send("""{"type":"hello_ack","server_time":1700000000.0}""")
                }
            }
        }))

        val uploader = EventUploader(
            serverUrl = server.url("/ingest-events").toString(),
            deviceName = "test-galaxy",
            spool = spool,
            listener = object : EventUploaderListener {
                override fun onStateChanged(state: ConnectionState) {
                    if (state == ConnectionState.CONNECTED) {
                        connectedDeferred.complete(Unit)
                    }
                }
            }
        )

        uploader.start()
        withTimeout(5000) { connectedDeferred.await() }
        uploader.stop()

        // STOP_GRACE_MS later stop() force-cancels the socket: that late failure must not
        // overwrite TERMINATED or trigger a reconnect.
        delay(2000)
        assertThat(uploader.connectionState).isEqualTo(ConnectionState.TERMINATED)
        assertThat(server.requestCount).isEqualTo(1)
    }

    @Test
    fun `downlink treat triggers listener`() = runBlocking {        val treatReceived = CompletableDeferred<Double>()

        server.enqueue(MockResponse().withWebSocketUpgrade(object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) {
                if (text.contains(""""type":"hello"""")) {
                    webSocket.send("""{"type":"hello_ack","server_time":1700000000.0}""")
                    webSocket.send("""{"type":"treat","ts":1700000005.5}""")
                }
            }
        }))

        val uploader = EventUploader(
            serverUrl = server.url("/ingest-events").toString(),
            deviceName = "test-galaxy",
            spool = spool,
            listener = object : EventUploaderListener {
                override fun onTreatDownlink(ts: Double) {
                    treatReceived.complete(ts)
                }
            }
        )

        uploader.start()

        val treatTs = withTimeout(5000) { treatReceived.await() }
        assertThat(treatTs).isEqualTo(1700000005.5)

        uploader.stop()
    }

    @Test
    fun `downlink treat reads the phone-clock ts from data`() = runBlocking {
        val treatReceived = CompletableDeferred<Double>()

        server.enqueue(MockResponse().withWebSocketUpgrade(object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) {
                if (text.contains(""""type":"hello"""")) {
                    webSocket.send("""{"type":"hello_ack","server_time":1700000000.0}""")
                    // the real server shape: the treat ts lives under data, on the phone's clock
                    webSocket.send("""{"type":"treat","data":{"ts":123.5,"offset_s":2.0}}""")
                }
            }
        }))

        val uploader = EventUploader(
            serverUrl = server.url("/ingest-events").toString(),
            deviceName = "test-galaxy",
            spool = spool,
            listener = object : EventUploaderListener {
                override fun onTreatDownlink(ts: Double) {
                    treatReceived.complete(ts)
                }
            }
        )

        uploader.start()

        val treatTs = withTimeout(5000) { treatReceived.await() }
        assertThat(treatTs).isEqualTo(123.5)  // not the phone clock fallback

        uploader.stop()
    }

    @Test
    fun `station hello is proto 2 and the challenge is answered with a signature`() = runBlocking {
        val helloReceived = CompletableDeferred<String>()
        val authReceived = CompletableDeferred<String>()
        val signer = SoftwareSigner()

        server.enqueue(MockResponse().withWebSocketUpgrade(object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) {
                if (text.contains(""""type":"hello"""")) {
                    helloReceived.complete(text)
                    webSocket.send("""{"type":"challenge","nonce":"MTIzNDU2Nzg5MDEyMzQ1Njc4OTAxMjM0NTY3ODkwMTI=","server_time":1700000000.0}""")
                } else if (text.contains(""""type":"auth"""")) {
                    authReceived.complete(text)
                }
            }
        }))

        val uploader = EventUploader(
            serverUrl = server.url("/ingest-events").toString(),
            deviceName = "Pixel 8",
            spool = spool,
            stationId = "st1",
            signer = signer,
        )

        uploader.start()
        val hello = Json.parseToJsonElement(withTimeout(5000) { helloReceived.await() }).jsonObject
        assertThat(hello["proto"]?.jsonPrimitive?.content?.toInt()).isEqualTo(2)
        assertThat(hello["station_id"]?.jsonPrimitive?.content).isEqualTo("st1")

        val auth = withTimeout(5000) { authReceived.await() }
        val authObj = Json.parseToJsonElement(auth).jsonObject
        assertThat(authObj["sig"]?.jsonPrimitive?.content?.isNotBlank()).isTrue()

        // the signature verifies against the same public key the app registered
        val expected = StationPairing.authMessage("MTIzNDU2Nzg5MDEyMzQ1Njc4OTAxMjM0NTY3ODkwMTI=", "st1", "Pixel 8")
        val sig = java.util.Base64.getDecoder().decode(authObj["sig"]?.jsonPrimitive?.content)
        val verifies = java.security.Signature.getInstance("SHA256withECDSA").run {
            initVerify(signer.publicKey)
            update(expected)
            verify(sig)
        }
        assertThat(verifies).isTrue()

        uploader.stop()
    }

    @Test
    fun `closed with 4410 fires onPairingInvalid and does not reconnect`() = runBlocking {
        val invalidReason = CompletableDeferred<String>()

        server.enqueue(MockResponse().withWebSocketUpgrade(object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) {
                if (text.contains(""""type":"hello"""")) {
                    webSocket.close(4410, "station removed")
                }
            }
        }))

        val uploader = EventUploader(
            serverUrl = server.url("/ingest-events").toString(),
            deviceName = "Pixel 8",
            spool = spool,
            listener = object : EventUploaderListener {
                override fun onPairingInvalid(reason: String) {
                    invalidReason.complete(reason)
                }
            },
        )

        uploader.start()
        assertThat(withTimeout(5000) { invalidReason.await() }).isEqualTo("station removed")
        assertThat(uploader.connectionState).isEqualTo(ConnectionState.TERMINATED)
        assertThat(server.requestCount).isEqualTo(1)  // no reconnect after a revoke
    }
}
