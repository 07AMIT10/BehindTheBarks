package com.btb.ondevice.net

import com.btb.ondevice.contracts.AudioEvent
import com.btb.ondevice.contracts.ContractsJson
import com.btb.ondevice.contracts.FrameEvent
import com.btb.ondevice.contracts.RulesLabel
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.double
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import okio.ByteString.Companion.toByteString
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger
import kotlin.math.min

enum class ConnectionState {
    DISCONNECTED,
    CONNECTING,
    CONNECTED,
    TERMINATED,
}

interface EventUploaderListener {
    fun onStateChanged(state: ConnectionState) {}
    fun onTreatDownlink(ts: Double) {}
    fun onConfigDownlink(configJson: String) {}
}

/**
 * Uploads on-device perception events over WebSocket to /ingest-events.
 *
 * Implements:
 *  - Hello / HelloAck handshake with proto: 1.
 *  - Periodic pings for clock-skew compensation.
 *  - Envelopes for frame, audio, rules, treat, and binary JPEG preview (0x01 + JPEG).
 *  - Backpressure: drops preview first, then frame events if OkHttp queue > 256 KB.
 *  - Event spooling: saves non-frame events to [spool] when disconnected, flushes on reconnect.
 *  - Reconnection with exponential backoff (0.5s, 1s, 2s, 4s, 5s).
 *  - Permanent closure on codes 4400 (bad hello), 4408 (replaced), 4409 (busy), 1000 (normal).
 */
class EventUploader(
    val serverUrl: String,
    val deviceName: String,
    val spool: EventSpool? = null,
    val okHttpClient: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(5, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .build(),
    val pingIntervalMs: Long = 15_000L,
    val previewMinIntervalMs: Long = 500L, // Max 2 fps preview
    val maxQueueBytes: Long = 256 * 1024L, // 256 KB
    val listener: EventUploaderListener? = null,
    val clock: () -> Double = { System.currentTimeMillis() / 1000.0 },
    private val scope: CoroutineScope = CoroutineScope(Dispatchers.IO),
) {
    companion object {
        const val KIND_PREVIEW: Byte = 0x01
        const val CLOSE_BAD_HELLO = 4400
        const val CLOSE_REPLACED = 4408
        const val CLOSE_BUSY = 4409
        const val CLOSE_NORMAL = 1000

        val TERMINAL_CLOSE_CODES = setOf(
            CLOSE_NORMAL,
            CLOSE_BAD_HELLO,
            CLOSE_REPLACED,
            CLOSE_BUSY,
        )

        val BACKOFF_STEPS_MS = listOf(500L, 1000L, 2000L, 4000L, 5000L)
    }

    private var webSocket: WebSocket? = null
    private var state = ConnectionState.DISCONNECTED
    private val isRunning = AtomicBoolean(false)
    private var backoffIndex = AtomicInteger(0)
    private var reconnectJob: Job? = null
    private var pingJob: Job? = null
    private var lastPreviewMs = 0L
    private val lock = Any()

    val connectionState: ConnectionState get() = synchronized(lock) { state }

    fun start() {
        if (isRunning.compareAndSet(false, true)) {
            backoffIndex.set(0)
            connect()
        }
    }

    fun stop() {
        if (isRunning.compareAndSet(true, false)) {
            synchronized(lock) {
                state = ConnectionState.TERMINATED
                reconnectJob?.cancel()
                pingJob?.cancel()
                try {
                    webSocket?.close(CLOSE_NORMAL, "Client stopped")
                } catch (_: Exception) {}
                try {
                    webSocket?.cancel()
                } catch (_: Exception) {}
                webSocket = null
            }
            listener?.onStateChanged(ConnectionState.TERMINATED)
        }
    }

    private fun setState(newState: ConnectionState) {
        synchronized(lock) {
            if (state == ConnectionState.TERMINATED) return
            state = newState
        }
        listener?.onStateChanged(newState)
    }

    private fun connect() {
        if (!isRunning.get()) return

        setState(ConnectionState.CONNECTING)

        val httpUrl = if (serverUrl.startsWith("ws://")) {
            "http://" + serverUrl.removePrefix("ws://")
        } else if (serverUrl.startsWith("wss://")) {
            "https://" + serverUrl.removePrefix("wss://")
        } else {
            serverUrl
        }

        try {
            val request = Request.Builder().url(httpUrl).build()
            webSocket = okHttpClient.newWebSocket(request, object : WebSocketListener() {
                override fun onOpen(webSocket: WebSocket, response: Response) {
                    // Send hello handshake immediately
                    val phoneTime = clock()
                    val helloJson = """{"type":"hello","proto":1,"device":"$deviceName","phone_time":$phoneTime,"models":{},"profile":"mobile"}"""
                    webSocket.send(helloJson)
                }

                override fun onMessage(webSocket: WebSocket, text: String) {
                    handleServerText(webSocket, text)
                }

                override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                    try {
                        webSocket.close(code, reason)
                    } catch (_: Exception) {}
                    handleClose(code, reason)
                }

                override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                    handleClose(code, reason)
                }

                override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                    handleFailure(t)
                }
            })
        } catch (e: Exception) {
            handleFailure(e)
        }
    }

    private fun handleServerText(webSocket: WebSocket, text: String) {
        try {
            val root = Json.parseToJsonElement(text).jsonObject
            val type = root["type"]?.jsonPrimitive?.content ?: return

            when (type) {
                "hello_ack" -> {
                    synchronized(lock) {
                        state = ConnectionState.CONNECTED
                    }
                    backoffIndex.set(0)
                    listener?.onStateChanged(ConnectionState.CONNECTED)

                    // Start pings and flush spool
                    startPings()
                    flushSpool()
                }
                "pong" -> {
                    // Clock skew offset handled by server
                }
                "treat" -> {
                    val ts = root["ts"]?.jsonPrimitive?.double ?: clock()
                    listener?.onTreatDownlink(ts)
                }
                "config" -> {
                    val data = root["data"]?.toString() ?: ""
                    listener?.onConfigDownlink(data)
                }
            }
        } catch (_: Exception) {
        }
    }

    private fun handleClose(code: Int, reason: String) {
        pingJob?.cancel()
        synchronized(lock) {
            webSocket = null
            if (code in TERMINAL_CLOSE_CODES) {
                state = ConnectionState.TERMINATED
                isRunning.set(false)
            } else {
                state = ConnectionState.DISCONNECTED
            }
        }
        listener?.onStateChanged(connectionState)

        if (isRunning.get() && code !in TERMINAL_CLOSE_CODES) {
            scheduleReconnect()
        }
    }

    private fun handleFailure(t: Throwable) {
        try {
            android.util.Log.e("EventUploader", "WebSocket failed ($serverUrl): ${t.message}", t)
        } catch (_: Throwable) {}
        pingJob?.cancel()
        synchronized(lock) {
            webSocket = null
            state = ConnectionState.DISCONNECTED
        }
        listener?.onStateChanged(ConnectionState.DISCONNECTED)

        if (isRunning.get()) {
            scheduleReconnect()
        }
    }

    private fun scheduleReconnect() {
        reconnectJob?.cancel()
        reconnectJob = scope.launch {
            val idx = backoffIndex.getAndIncrement()
            val delayMs = BACKOFF_STEPS_MS[min(idx, BACKOFF_STEPS_MS.size - 1)]
            delay(delayMs)
            if (isRunning.get()) {
                connect()
            }
        }
    }

    private fun startPings() {
        pingJob?.cancel()
        pingJob = scope.launch {
            while (isActive && connectionState == ConnectionState.CONNECTED) {
                delay(pingIntervalMs)
                if (connectionState == ConnectionState.CONNECTED) {
                    val pingJson = """{"type":"ping","phone_time":${clock()}}"""
                    webSocket?.send(pingJson)
                }
            }
        }
    }

    private fun flushSpool() {
        val sp = spool ?: return
        val currentTs = clock()
        val events = sp.drain(currentTs)
        for (ev in events) {
            val sent = sendEnvelope(ev.type, ev.json)
            if (!sent) {
                // Re-enqueue remaining
                sp.enqueue(ev.type, ev.ts, ev.json)
                break
            }
        }
    }

    // -- Event Sending Methods ------------------------------------------------------------------

    fun sendFrame(frame: FrameEvent): Boolean {
        if (connectionState != ConnectionState.CONNECTED) {
            // Frames are dropped when disconnected (not spooled)
            return false
        }
        val ws = webSocket ?: return false
        // Backpressure check: drop frame if queue > maxQueueBytes
        if (ws.queueSize() > maxQueueBytes) {
            return false
        }
        val jsonPayload = ContractsJson.encodeToString(FrameEvent.serializer(), frame)
        return sendEnvelope("frame", jsonPayload)
    }

    fun sendAudio(audio: AudioEvent): Boolean {
        val jsonPayload = ContractsJson.encodeToString(AudioEvent.serializer(), audio)
        if (connectionState == ConnectionState.CONNECTED) {
            val sent = sendEnvelope("audio", jsonPayload)
            if (sent) return true
        }
        // Spool when disconnected or send failed
        return spool?.enqueue("audio", audio.ts, jsonPayload) ?: false
    }

    fun sendRules(rules: RulesLabel): Boolean {
        val jsonPayload = ContractsJson.encodeToString(RulesLabel.serializer(), rules)
        if (connectionState == ConnectionState.CONNECTED) {
            val sent = sendEnvelope("rules", jsonPayload)
            if (sent) return true
        }
        return spool?.enqueue("rules", rules.ts, jsonPayload) ?: false
    }

    fun sendTreat(ts: Double = clock()): Boolean {
        val jsonPayload = """{"ts":$ts}"""
        if (connectionState == ConnectionState.CONNECTED) {
            val sent = sendEnvelope("treat", jsonPayload)
            if (sent) return true
        }
        return spool?.enqueue("treat", ts, jsonPayload) ?: false
    }

    fun sendPreview(jpeg: ByteArray, ts: Double = clock()): Boolean {
        if (connectionState != ConnectionState.CONNECTED) {
            return false
        }
        val ws = webSocket ?: return false

        // Rate limit preview: max 2 fps
        val nowMs = System.currentTimeMillis()
        if (nowMs - lastPreviewMs < previewMinIntervalMs) {
            return false
        }

        // Backpressure: drop preview first if queue > maxQueueBytes
        if (ws.queueSize() > maxQueueBytes) {
            return false
        }

        lastPreviewMs = nowMs
        val msg = ByteArray(1 + jpeg.size)
        msg[0] = KIND_PREVIEW
        System.arraycopy(jpeg, 0, msg, 1, jpeg.size)
        return ws.send(msg.toByteString())
    }

    private fun sendEnvelope(type: String, jsonPayload: String): Boolean {
        val ws = webSocket ?: return false
        val envelope = """{"type":"$type","data":$jsonPayload}"""
        return ws.send(envelope)
    }
}
