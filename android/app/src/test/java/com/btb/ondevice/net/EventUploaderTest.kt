package com.btb.ondevice.net

import com.btb.ondevice.contracts.AudioEvent
import com.btb.ondevice.contracts.AudioLabel
import com.btb.ondevice.contracts.FrameEvent
import com.btb.ondevice.pipeline.StubPerception
import com.google.common.truth.Truth.assertThat
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
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
        } catch (_: Exception) {}
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
    fun `downlink treat triggers listener`() = runBlocking {
        val treatReceived = CompletableDeferred<Double>()

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
}
