package com.btb.ondevice.net

import com.btb.ondevice.capture.RaiseResult
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
import okhttp3.OkHttpClient
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
    fun `hello_ack notifies the listener once so the station can report its mode`() = runBlocking {
        val helloReceived = CompletableDeferred<Unit>()
        val ackSeen = CompletableDeferred<Int>()
        var acks = 0

        server.enqueue(MockResponse().withWebSocketUpgrade(object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) {
                if (text.contains(""""type":"hello"""")) {
                    helloReceived.complete(Unit)
                    webSocket.send("""{"type":"hello_ack","server_time":1700000000.0}""")
                }
            }
        }))

        val uploader = EventUploader(
            serverUrl = server.url("/ingest-events").toString(),
            deviceName = "test-galaxy",
            spool = spool,
            listener = object : EventUploaderListener {
                override fun onHelloAck() {
                    acks += 1
                    if (!ackSeen.isCompleted) ackSeen.complete(acks)
                }
            }
        )

        uploader.start()
        withTimeout(5000) { helloReceived.await() }
        assertThat(withTimeout(5000) { ackSeen.await() }).isEqualTo(1)
        delay(300)
        assertThat(acks).isEqualTo(1)
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

    @Test
    fun `policy downlink carries mode preview audio and viewer counts to the listener`() = runBlocking {
        val connected = CompletableDeferred<Unit>()
        val gotPolicy = CompletableDeferred<List<Any>>()
        server.enqueue(MockResponse().withWebSocketUpgrade(object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) {
                if (text.contains(""""type":"hello"""")) {
                    webSocket.send("""{"type":"hello_ack","server_time":1700000000.0}""")
                    webSocket.send(
                        """{"type":"policy","data":{"mode":"limited",""" +
                        """"preview":{"fps":3,"max_edge":480,"quality":70},""" +
                        """"audio":true,"viewers":{"video":2,"audio":1}}}"""
                    )
                }
            }
        }))

        val uploader = EventUploader(
            serverUrl = server.url("/ingest-events").toString(),
            deviceName = "test-galaxy",
            spool = spool,
            listener = object : EventUploaderListener {
                override fun onStateChanged(state: ConnectionState) {
                    if (state == ConnectionState.CONNECTED) connected.complete(Unit)
                }
                override fun onPolicyDownlink(
                    mode: String, previewFps: Int, previewMaxEdge: Int, previewQuality: Int,
                    audio: Boolean, videoViewers: Int, audioViewers: Int,
                ) {
                    gotPolicy.complete(listOf(mode, previewFps, previewMaxEdge, previewQuality, audio, videoViewers, audioViewers))
                }
            }
        )
        uploader.start()
        withTimeout(5000) { connected.await() }
        val p = withTimeout(5000) { gotPolicy.await() }
        assertThat(p[0]).isEqualTo("limited")
        assertThat(p[1]).isEqualTo(3)
        assertThat(p[2]).isEqualTo(480)
        assertThat(p[3]).isEqualTo(70)
        assertThat(p[4]).isEqualTo(true)
        assertThat(p[5]).isEqualTo(2)
        assertThat(p[6]).isEqualTo(1)
        uploader.stop()
    }

    @Test
    fun `mode_request downlink is forwarded with the requesting role`() = runBlocking {
        val connected = CompletableDeferred<Unit>()
        val gotRequest = CompletableDeferred<Pair<String, String>>()
        server.enqueue(MockResponse().withWebSocketUpgrade(object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) {
                if (text.contains(""""type":"hello"""")) {
                    webSocket.send("""{"type":"hello_ack","server_time":1700000000.0}""")
                    webSocket.send("""{"type":"mode_request","data":{"mode":"full","from":"owner"}}""")
                }
            }
        }))

        val uploader = EventUploader(
            serverUrl = server.url("/ingest-events").toString(),
            deviceName = "test-galaxy",
            spool = spool,
            listener = object : EventUploaderListener {
                override fun onStateChanged(state: ConnectionState) {
                    if (state == ConnectionState.CONNECTED) connected.complete(Unit)
                }
                override fun onModeRequest(mode: String, from: String) {
                    gotRequest.complete(mode to from)
                }
            }
        )
        uploader.start()
        withTimeout(5000) { connected.await() }
        val (mode, from) = withTimeout(5000) { gotRequest.await() }
        assertThat(mode).isEqualTo("full")
        assertThat(from).isEqualTo("owner")
        uploader.stop()
    }

    @Test
    fun `sendModeState reports the station cap and effective mode upstream`() = runBlocking {
        val connected = CompletableDeferred<Unit>()
        val modeState = CompletableDeferred<String>()
        server.enqueue(MockResponse().withWebSocketUpgrade(object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) {
                when {
                    text.contains(""""type":"hello"""") ->
                        webSocket.send("""{"type":"hello_ack","server_time":1700000000.0}""")
                    text.contains(""""type":"mode_state"""") -> modeState.complete(text)
                }
            }
        }))

        val uploader = EventUploader(
            serverUrl = server.url("/ingest-events").toString(),
            deviceName = "test-galaxy",
            spool = spool,
            listener = object : EventUploaderListener {
                override fun onStateChanged(state: ConnectionState) {
                    if (state == ConnectionState.CONNECTED) connected.complete(Unit)
                }
            }
        )
        uploader.start()
        withTimeout(5000) { connected.await() }
        assertThat(uploader.sendModeState("privacy", "privacy", "station")).isTrue()
        val sent = withTimeout(5000) { modeState.await() }
        assertThat(sent).contains(""""cap":"privacy"""")
        assertThat(sent).contains(""""effective":"privacy"""")
        assertThat(sent).contains(""""reason":"station"""")
        uploader.stop()
    }

    @Test
    fun `sendModeResult reports how a raise prompt ended upstream`() = runBlocking {
        val connected = CompletableDeferred<Unit>()
        val results = java.util.concurrent.LinkedBlockingQueue<String>()
        server.enqueue(MockResponse().withWebSocketUpgrade(object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) {
                when {
                    text.contains(""""type":"hello"""") ->
                        webSocket.send("""{"type":"hello_ack","server_time":1700000000.0}""")
                    text.contains(""""type":"mode_result"""") -> results.add(text)
                }
            }
        }))

        val uploader = EventUploader(
            serverUrl = server.url("/ingest-events").toString(),
            deviceName = "test-galaxy",
            spool = spool,
            listener = object : EventUploaderListener {
                override fun onStateChanged(state: ConnectionState) {
                    if (state == ConnectionState.CONNECTED) connected.complete(Unit)
                }
            }
        )
        uploader.start()
        withTimeout(5000) { connected.await() }
        // The three answers the station can give to a raise prompt (MonitorService.resolveRaise).
        for (result in listOf(RaiseResult.ALLOWED, RaiseResult.DENIED, RaiseResult.TIMEOUT)) {
            assertThat(uploader.sendModeResult("full", result.wire)).isTrue()
        }
        val sent = List(3) { withTimeout(5000) { results.poll(5, java.util.concurrent.TimeUnit.SECONDS) } }
        assertThat(sent[0]).isEqualTo("""{"type":"mode_result","data":{"mode":"full","result":"allowed"}}""")
        assertThat(sent[1]).isEqualTo("""{"type":"mode_result","data":{"mode":"full","result":"denied"}}""")
        assertThat(sent[2]).isEqualTo("""{"type":"mode_result","data":{"mode":"full","result":"timeout"}}""")
        uploader.stop()
    }

    /** A full ADPCM audio frame for tests: 0x02 | codec 1 | seq 7 | t_ms 1234 | 804-byte block. */
    private fun adpcmFrame(seq: Long = 7L): ByteArray {
        val samples = ShortArray(com.btb.ondevice.audio.AdpcmCodec.CHUNK_SAMPLES) { (it % 300).toShort() }
        return com.btb.ondevice.audio.AdpcmCodec.packFrame(
            com.btb.ondevice.audio.AdpcmCodec.CODEC_IMA_ADPCM, seq, 1234L,
            com.btb.ondevice.audio.AdpcmCodec.encodeBlock(samples),
        )
    }

    @Test
    fun `sendAudioChunk sends a binary 0x02 ADPCM frame of 814 bytes and counts it`() = runBlocking {
        val binaries = CompletableDeferred<ByteString>()
        val connected = CompletableDeferred<Unit>()
        server.enqueue(MockResponse().withWebSocketUpgrade(object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) {
                if (text.contains(""""type":"hello"""")) {
                    webSocket.send("""{"type":"hello_ack","server_time":1700000000.0}""")
                }
            }

            override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
                binaries.complete(bytes)
            }
        }))
        val uploader = EventUploader(
            serverUrl = server.url("/ingest-events").toString(),
            deviceName = "test-galaxy",
            spool = spool,
            listener = object : EventUploaderListener {
                override fun onStateChanged(state: ConnectionState) {
                    if (state == ConnectionState.CONNECTED) connected.complete(Unit)
                }
            }
        )
        uploader.start()
        withTimeout(5000) { connected.await() }

        val frame = adpcmFrame()
        assertThat(uploader.sendAudioChunk(frame)).isTrue()

        val received = withTimeout(5000) { binaries.await() }
        assertThat(received.size).isEqualTo(814)
        assertThat(received[0]).isEqualTo(EventUploader.KIND_AUDIO)
        assertThat(received[1]).isEqualTo(1.toByte())
        assertThat(uploader.audioFramesSent.get()).isEqualTo(1L)
        assertThat(uploader.audioBytesSent.get()).isEqualTo(814L)
        assertThat(uploader.audioDroppedBackpressure.get()).isEqualTo(0L)
        uploader.stop()
    }

    @Test
    fun `sendAudioChunk refuses when not connected and sends nothing`() {
        val uploader = EventUploader(
            serverUrl = server.url("/ingest-events").toString(),
            deviceName = "test-galaxy",
            spool = spool,
        )
        assertThat(uploader.sendAudioChunk(adpcmFrame())).isFalse()
        assertThat(uploader.audioFramesSent.get()).isEqualTo(0L)
        assertThat(uploader.audioDroppedBackpressure.get()).isEqualTo(0L)
    }

    @Test
    fun `sendAudioChunk drops and counts a chunk over the queue limit`() = runBlocking {
        val connected = CompletableDeferred<Unit>()
        server.enqueue(MockResponse().withWebSocketUpgrade(object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) {
                if (text.contains(""""type":"hello"""")) {
                    webSocket.send("""{"type":"hello_ack","server_time":1700000000.0}""")
                }
            }
        }))
        // A negative limit makes every chunk "backed up" (queueSize() is never below zero), so the
        // drop path is exercised deterministically without racing the socket writer.
        val uploader = EventUploader(
            serverUrl = server.url("/ingest-events").toString(),
            deviceName = "test-galaxy",
            spool = spool,
            audioMaxQueueBytes = -1L,
            listener = object : EventUploaderListener {
                override fun onStateChanged(state: ConnectionState) {
                    if (state == ConnectionState.CONNECTED) connected.complete(Unit)
                }
            }
        )
        uploader.start()
        withTimeout(5000) { connected.await() }

        assertThat(uploader.sendAudioChunk(adpcmFrame(1))).isFalse()
        assertThat(uploader.sendAudioChunk(adpcmFrame(2))).isFalse()
        assertThat(uploader.audioDroppedBackpressure.get()).isEqualTo(2L)
        assertThat(uploader.audioFramesSent.get()).isEqualTo(0L)
        uploader.stop()
    }

    @Test
    fun `sendAudioChunk rejects anything that is not an audio frame`() = runBlocking {
        val connected = CompletableDeferred<Unit>()
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
                    if (state == ConnectionState.CONNECTED) connected.complete(Unit)
                }
            }
        )
        uploader.start()
        withTimeout(5000) { connected.await() }

        val preview = adpcmFrame().also { it[0] = EventUploader.KIND_PREVIEW }
        assertThat(uploader.sendAudioChunk(preview)).isFalse()
        assertThat(uploader.sendAudioChunk(ByteArray(4))).isFalse()
        assertThat(uploader.audioFramesSent.get()).isEqualTo(0L)
        uploader.stop()
    }

    // -- I2: audio snippet upload ---------------------------------------------------------------

    /** Await a request without hanging the suite when nothing arrives. */
    private fun awaitRequest(seconds: Long = 5) = server.takeRequest(seconds, java.util.concurrent.TimeUnit.SECONDS)

    @Test
    fun `snippet post carries the snippet station and bearer auth headers`() = runBlocking {
        server.enqueue(MockResponse().setBody("ok"))

        val uploader = EventUploader(
            serverUrl = server.url("/ingest-events").toString(),
            deviceName = "test-galaxy",
            token = "tok-123",
            stationId = "st1",
            spool = spool,
        )

        uploader.uploadAudioSnippet("snip_st1_1", ByteArray(16))

        val request = awaitRequest()
        assertThat(request).isNotNull()
        assertThat(request!!.path).isEqualTo("/events/audio-snippet")
        assertThat(request.getHeader("X-Snippet-ID")).isEqualTo("snip_st1_1")
        assertThat(request.getHeader("X-Station-ID")).isEqualTo("st1")
        assertThat(request.getHeader("Authorization")).isEqualTo("Bearer tok-123")
        assertThat(request.getHeader("Content-Type")).startsWith("audio/wav")
        assertThat(request.bodySize).isEqualTo(16L)

        uploader.stop()
    }

    @Test
    fun `a tokenless station sends no auth header on the snippet post`() = runBlocking {
        server.enqueue(MockResponse().setBody("ok"))

        val uploader = EventUploader(
            serverUrl = server.url("/ingest-events").toString(),
            deviceName = "test-galaxy",
            spool = spool,
        )

        uploader.uploadAudioSnippet("snip_1", ByteArray(16))

        val request = awaitRequest()
        assertThat(request).isNotNull()
        assertThat(request!!.path).isEqualTo("/events/audio-snippet")
        assertThat(request.getHeader("X-Snippet-ID")).isEqualTo("snip_1")
        assertThat(request.getHeader("X-Station-ID")).isNull()
        assertThat(request.getHeader("Authorization")).isNull()

        uploader.stop()
    }

    @Test
    fun `stalled snippets serialize - one in flight, one waiting, newest wins`() = runBlocking {
        // The server holds every answer for 3s (headers included, so even the status line never
        // arrives in time); the injected snippet client gives up after 200ms.
        val stall = MockResponse().setBody("ok").setHeadersDelay(3, java.util.concurrent.TimeUnit.SECONDS)
        server.enqueue(stall)
        server.enqueue(stall)

        val uploader = EventUploader(
            serverUrl = server.url("/ingest-events").toString(),
            deviceName = "test-galaxy",
            spool = spool,
            snippetOkHttpClient = OkHttpClient.Builder()
                .connectTimeout(2, java.util.concurrent.TimeUnit.SECONDS)
                .readTimeout(200, java.util.concurrent.TimeUnit.MILLISECONDS)
                .build(),
        )

        uploader.uploadAudioSnippet("s1", ByteArray(8))
        // A burst while the first POST is parked: the newest waits, the middle one is dropped.
        uploader.uploadAudioSnippet("s2", ByteArray(8))
        uploader.uploadAudioSnippet("s3", ByteArray(8))
        assertThat(uploader.snippetUploadsDropped).isEqualTo(1)
        assertThat(uploader.snippetUploadsBusy).isTrue()

        // Only the first POST is out so far, nothing else may run concurrently.
        val first = awaitRequest()
        assertThat(first!!.getHeader("X-Snippet-ID")).isEqualTo("s1")

        // The read timeout ends post #1; the gate then runs the newest waiting snippet (s3), not s2.
        withTimeout(10000) {
            while (server.requestCount < 2) kotlinx.coroutines.delay(25)
        }
        val second = awaitRequest()
        assertThat(second!!.getHeader("X-Snippet-ID")).isEqualTo("s3")

        withTimeout(10000) {
            while (uploader.snippetUploadsBusy) kotlinx.coroutines.delay(25)
        }
        assertThat(uploader.snippetUploadsDropped).isEqualTo(1)

        uploader.stop()
    }

    @Test
    fun `snippet post failure frees the gate for the next bark`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(503))
        server.enqueue(MockResponse().setBody("ok"))

        val uploader = EventUploader(
            serverUrl = server.url("/ingest-events").toString(),
            deviceName = "test-galaxy",
            spool = spool,
        )

        uploader.uploadAudioSnippet("bad", ByteArray(8))
        val first = awaitRequest()
        assertThat(first!!.getHeader("X-Snippet-ID")).isEqualTo("bad")
        withTimeout(5000) { while (uploader.snippetUploadsBusy) kotlinx.coroutines.delay(25) }
        assertThat(server.requestCount).isEqualTo(1)

        // The failed POST released the gate: the next bark starts a fresh upload.
        uploader.uploadAudioSnippet("good", ByteArray(8))
        val second = awaitRequest()
        assertThat(second!!.getHeader("X-Snippet-ID")).isEqualTo("good")
        assertThat(second!!.path).isEqualTo("/events/audio-snippet")
        assertThat(uploader.snippetUploadsDropped).isEqualTo(0)

        uploader.stop()
    }
}
