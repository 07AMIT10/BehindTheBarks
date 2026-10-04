package com.btb.ondevice

import com.google.common.truth.Truth.assertThat
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.currentTime
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.withTimeout
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.jupiter.api.Test
import java.net.ProtocolException
import java.util.concurrent.TimeUnit

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
    fun `websocket handshake to ingest-events completes on 101 and the hello_ack round-trips`(): Unit =
        runBlocking {
            val ack = HelloAck("hello_ack", 1727340000.5)
            val ackJson = Json.encodeToString(ack)
            val server = MockWebServer().apply { start() }
            val client = OkHttpClient()
            var socket: WebSocket? = null
            try {
                server.enqueue(
                    MockResponse().withWebSocketUpgrade(
                        object : WebSocketListener() {
                            override fun onOpen(webSocket: WebSocket, response: Response) {
                                webSocket.send(ackJson)
                            }
                        },
                    ),
                )
                val received = CompletableDeferred<String>()
                val failed = CompletableDeferred<Throwable>()
                socket = client.newWebSocket(
                    Request.Builder().url(server.url("/ingest-events")).build(),
                    object : WebSocketListener() {
                        override fun onMessage(webSocket: WebSocket, text: String) {
                            received.complete(text)
                        }

                        override fun onFailure(
                            webSocket: WebSocket,
                            t: Throwable,
                            response: Response?,
                        ) {
                            failed.complete(t)
                        }
                    },
                )

                val recorded =
                    requireNotNull(server.takeRequest(10, TimeUnit.SECONDS)) {
                        "the client never sent an upgrade request"
                    }

                assertThat(recorded.path).isEqualTo("/ingest-events")
                assertThat(recorded.getHeader("Upgrade")).isEqualTo("websocket")
                assertThat(received.isCompleted).isFalse()

                val body = withTimeout(10_000) { received.await() }

                assertThat(failed.isCompleted).isFalse()
                assertThat(body).isEqualTo(ackJson)
                assertThat(Json.decodeFromString<HelloAck>(body)).isEqualTo(ack)
            } finally {
                socket?.cancel()
                client.dispatcher.executorService.shutdown()
                server.shutdown()
            }
        }

    @Test
    fun `a non-101 upgrade response is rejected, so a completed handshake means 101`(): Unit =
        runBlocking {
            val server = MockWebServer().apply { start() }
            val client = OkHttpClient()
            var socket: WebSocket? = null
            try {
                server.enqueue(MockResponse().setResponseCode(404))
                val failed = CompletableDeferred<Throwable>()
                socket = client.newWebSocket(
                    Request.Builder().url(server.url("/ingest-events")).build(),
                    object : WebSocketListener() {
                        override fun onFailure(
                            webSocket: WebSocket,
                            t: Throwable,
                            response: Response?,
                        ) {
                            failed.complete(t)
                        }
                    },
                )

                val error = withTimeout(10_000) { failed.await() }

                assertThat(error).isInstanceOf(ProtocolException::class.java)
                assertThat(error).hasMessageThat().contains("101")
                assertThat(error).hasMessageThat().contains("404")
            } finally {
                socket?.cancel()
                client.dispatcher.executorService.shutdown()
                server.shutdown()
            }
        }

    @OptIn(ExperimentalCoroutinesApi::class)
    @Test
    fun `runTest advances virtual time deterministically`() = runTest {
        val done = CompletableDeferred<String>()

        launch {
            delay(1_500)
            done.complete("envelope uploaded")
        }

        assertThat(done.isCompleted).isFalse()

        advanceUntilIdle()

        assertThat(done.isCompleted).isTrue()
        assertThat(done.await()).isEqualTo("envelope uploaded")
        assertThat(currentTime).isEqualTo(1_500L)
    }
}

@Serializable
private data class Envelope(val type: String, val data: FrameData)

@Serializable
private data class FrameData(val ts: Double, val bbox: List<Double>?)

@Serializable
private data class HelloAck(val type: String, @SerialName("server_time") val serverTime: Double)