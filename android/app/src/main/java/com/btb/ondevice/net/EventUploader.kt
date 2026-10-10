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
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.double
import kotlinx.serialization.json.put
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
    /** The server accepted the handshake (hello_ack). The station reports its mode here (T3.1). */
    fun onHelloAck() {}
    fun onTreatDownlink(ts: Double) {}
    fun onConfigDownlink(configJson: String) {}
    fun onTorchDownlink(enabled: Boolean) {}
    fun onFlipDownlink() {}
    fun onPrivacyDownlink(enabled: Boolean) {}
    /** T2.5: the server refused the station's identity (4401) or revoked it (4410): pair again. */
    fun onPairingInvalid(reason: String) {}
    /** T3.1: the server's viewing policy for this household. The phone still applies its own cap:
     *  the effective mode is min(local cap, [mode]). */
    fun onPolicyDownlink(
        mode: String, previewFps: Int, previewMaxEdge: Int, previewQuality: Int,
        audio: Boolean, videoViewers: Int, audioViewers: Int,
    ) {}
    /** T3.1: the owner asked for a higher mode than the phone currently allows. Raising exposure
     *  always needs an on-device decision ([onPolicyDownlink] must not be used to bypass it). */
    fun onModeRequest(mode: String, from: String) {}
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
 *  - Permanent closure on codes 4400 (bad hello), 4401 (unauthorized), 4408 (replaced), 4409 (busy), 1000 (normal).
 */
class EventUploader(
    val serverUrl: String,
    val deviceName: String,
    val token: String? = null,
    /** Households mode (T2.5): set together, the hello becomes proto 2 and the challenge is signed. */
    val stationId: String? = null,
    val signer: Signer? = null,
    val spool: EventSpool? = null,
    val okHttpClient: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(5, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .build(),
    val pingIntervalMs: Long = 15_000L,
    var previewMinIntervalMs: Long = 500L,
    val maxQueueBytes: Long = 256 * 1024L, // 256 KB
    val listener: EventUploaderListener? = null,
    val clock: () -> Double = { System.currentTimeMillis() / 1000.0 },
    private val scope: CoroutineScope = CoroutineScope(Dispatchers.IO),
) {
    val previewDroppedBackpressure = java.util.concurrent.atomic.AtomicLong(0L)
    /** Previews that reached the backpressure gate (monotonic). The ladder's drop ratio divides by this. */
    val previewsOffered = java.util.concurrent.atomic.AtomicLong(0L)
    @Volatile
    var lastRttMs: Long = 0L
        private set
    private var lastPingSentMs: Long = 0L

    fun setPreviewMinInterval(intervalMs: Long) {
        previewMinIntervalMs = intervalMs.coerceAtLeast(0L)
    }

    fun queueBytes(): Long = webSocket?.queueSize() ?: 0L

    companion object {
        const val KIND_PREVIEW: Byte = 0x01
        const val CLOSE_BAD_HELLO = 4400
        const val CLOSE_UNAUTHORIZED = 4401
        const val CLOSE_REPLACED = 4408
        const val CLOSE_BUSY = 4409
        const val CLOSE_NORMAL = 1000
        const val PROTO_HELLO = 1
        const val CLOSE_REVOKED = 4410  // T2.4/T2.5: the owner revoked this station
        const val PROTO_HELLO_STATION = 2

        val TERMINAL_CLOSE_CODES = setOf(
            CLOSE_NORMAL,
            CLOSE_BAD_HELLO,
            CLOSE_UNAUTHORIZED,
            CLOSE_REPLACED,
            CLOSE_BUSY,
            CLOSE_REVOKED,
        )

        val BACKOFF_STEPS_MS = listOf(500L, 1000L, 2000L, 4000L, 5000L)

        /**
         * How long stop() waits before force-cancelling the socket. cancel() discards the queued
         * close frame (observed on the real phone as close code 1005 "Socket closed"), so the
         * close frame must get a moment on the wire first.
         */
        const val STOP_GRACE_MS = 1500L
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
        if (!isRunning.compareAndSet(true, false)) return
        val ws: WebSocket?
        synchronized(lock) {
            state = ConnectionState.TERMINATED
            reconnectJob?.cancel()
            pingJob?.cancel()
            ws = webSocket
            webSocket = null
        }
        if (ws != null) {
            // bye first: OkHttp rejects send() after close(), so the close frame would drop it.
            try { ws.send("""{"type":"bye"}""") } catch (_: Exception) {}
            try { ws.close(CLOSE_NORMAL, "Client stopped") } catch (_: Exception) {}
            // Never cancel immediately: cancel() discards the queued close frame (observed as code 1005).
            scope.launch {
                delay(STOP_GRACE_MS)
                try { ws.cancel() } catch (_: Exception) {}
            }
        }
        listener?.onStateChanged(ConnectionState.TERMINATED)
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

        synchronized(lock) {
            if (state == ConnectionState.CONNECTED || state == ConnectionState.CONNECTING) {
                return
            }
            state = ConnectionState.CONNECTING
        }
        listener?.onStateChanged(ConnectionState.CONNECTING)

        val httpUrl = if (serverUrl.startsWith("ws://")) {
            "http://" + serverUrl.removePrefix("ws://")
        } else if (serverUrl.startsWith("wss://")) {
            "https://" + serverUrl.removePrefix("wss://")
        } else {
            serverUrl
        }

        try {
            val request = Request.Builder().url(httpUrl).build()
            val socket = okHttpClient.newWebSocket(request, object : WebSocketListener() {
                override fun onOpen(ws: WebSocket, response: Response) {
                    synchronized(lock) {
                        if (webSocket != null && ws !== webSocket) return
                        webSocket = ws
                    }
                    try {
                        android.util.Log.i("EventUploader", "WebSocket connected to $serverUrl")
                    } catch (_: Throwable) {}
                    // Send hello handshake immediately (built, not interpolated: the device
                    // name is user text and must never break the JSON).
                    val phoneTime = clock()
                    val helloJson = buildJsonObject {
                        put("type", "hello")
                        put("proto", if (stationId != null) PROTO_HELLO_STATION else PROTO_HELLO)
                        put("device", deviceName)
                        put("phone_time", phoneTime)
                        put("models", buildJsonObject {})
                        put("profile", "mobile")
                        if (stationId != null) put("station_id", stationId)
                        if (!token.isNullOrBlank()) put("token", token)
                    }.toString()
                    val sent = ws.send(helloJson)
                    try {
                        android.util.Log.i("EventUploader", "Sent hello: $sent")
                    } catch (_: Throwable) {}
                }

                override fun onMessage(ws: WebSocket, text: String) {
                    synchronized(lock) {
                        if (webSocket != null && ws !== webSocket) return
                    }
                    handleServerText(ws, text)
                }

                override fun onClosing(ws: WebSocket, code: Int, reason: String) {
                    synchronized(lock) {
                        if (webSocket != null && ws !== webSocket) return
                    }
                    try {
                        ws.close(code, reason)
                    } catch (_: Exception) {}
                    handleClose(code, reason)
                }

                override fun onClosed(ws: WebSocket, code: Int, reason: String) {
                    synchronized(lock) {
                        if (webSocket != null && ws !== webSocket) return
                    }
                    handleClose(code, reason)
                }

                override fun onFailure(ws: WebSocket, t: Throwable, response: Response?) {
                    synchronized(lock) {
                        if (webSocket != null && ws !== webSocket) return
                    }
                    handleFailure(t)
                }
            })
            synchronized(lock) {
                webSocket = socket
            }
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
                    android.util.Log.i("EventUploader", "Received hello_ack from server")
                    synchronized(lock) {
                        state = ConnectionState.CONNECTED
                    }
                    backoffIndex.set(0)
                    listener?.onStateChanged(ConnectionState.CONNECTED)
                    listener?.onHelloAck()

                    // Start pings and flush spool
                    startPings()
                    flushSpool()
                }
                "challenge" -> {
                    // households mode: sign nonce|station_id|device so the server knows it is this station
                    val nonce = root["nonce"]?.jsonPrimitive?.content
                    val signer = this.signer
                    if (nonce != null && stationId != null && signer != null) {
                        try {
                            webSocket.send(StationPairing.authEnvelope(nonce, stationId, deviceName, signer))
                        } catch (_: Exception) {}
                    }
                }
                "pong" -> {
                    if (lastPingSentMs > 0L) {
                        lastRttMs = (System.currentTimeMillis() - lastPingSentMs).coerceAtLeast(0L)
                    }
                }
                "treat" -> {
                    // The server sends {"type":"treat","data":{"ts":<phone clock>,"offset_s":...}}; the
                    // legacy root-level ts is still honoured, and the phone clock is the last resort.
                    val ts = root["data"]?.jsonObject?.get("ts")?.jsonPrimitive?.double
                        ?: root["ts"]?.jsonPrimitive?.double
                        ?: clock()
                    listener?.onTreatDownlink(ts)
                }
                "config" -> {
                    val data = root["data"]?.toString() ?: ""
                    listener?.onConfigDownlink(data)
                }
                "torch" -> {
                    val enabled = root["data"]?.jsonObject?.get("enabled")?.jsonPrimitive?.content?.toBooleanStrictOrNull() ?: true
                    listener?.onTorchDownlink(enabled)
                }
                "flip" -> {
                    listener?.onFlipDownlink()
                }
                "privacy" -> {
                    val enabled = root["data"]?.jsonObject?.get("enabled")?.jsonPrimitive?.content?.toBooleanStrictOrNull() ?: true
                    listener?.onPrivacyDownlink(enabled)
                }
                "policy" -> {
                    val data = root["data"]?.jsonObject
                    val mode = data?.get("mode")?.jsonPrimitive?.content ?: return
                    val preview = data?.get("preview")?.jsonObject
                    val viewers = data?.get("viewers")?.jsonObject
                    listener?.onPolicyDownlink(
                        mode = mode,
                        previewFps = preview?.get("fps")?.jsonPrimitive?.content?.toIntOrNull() ?: 2,
                        previewMaxEdge = preview?.get("max_edge")?.jsonPrimitive?.content?.toIntOrNull() ?: 320,
                        previewQuality = preview?.get("quality")?.jsonPrimitive?.content?.toIntOrNull() ?: 60,
                        audio = data?.get("audio")?.jsonPrimitive?.content?.toBooleanStrictOrNull() ?: false,
                        videoViewers = viewers?.get("video")?.jsonPrimitive?.content?.toIntOrNull() ?: 0,
                        audioViewers = viewers?.get("audio")?.jsonPrimitive?.content?.toIntOrNull() ?: 0,
                    )
                }
                "mode_request" -> {
                    val data = root["data"]?.jsonObject
                    val mode = data?.get("mode")?.jsonPrimitive?.content ?: return
                    val from = data?.get("from")?.jsonPrimitive?.content ?: "owner"
                    listener?.onModeRequest(mode, from)
                }
            }
        } catch (e: Exception) {
            android.util.Log.e("EventUploader", "Error handling server text: ${e.message}", e)
        }
    }

    private fun handleClose(code: Int, reason: String) {
        // A deliberate stop() already set TERMINATED; a late close callback must not
        // overwrite it or notify the listener a second time.
        synchronized(lock) {
            if (state == ConnectionState.TERMINATED && !isRunning.get()) return
        }
        if (code == CLOSE_UNAUTHORIZED || code == CLOSE_REVOKED) {
            listener?.onPairingInvalid(reason)  // T2.5: the UI should offer to pair again
        }
        android.util.Log.w("EventUploader", "WebSocket closed ($serverUrl): code=$code, reason=$reason")
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
        // stop()'s deferred cancel() lands here after STOP_GRACE_MS; stay terminated.
        synchronized(lock) {
            if (state == ConnectionState.TERMINATED && !isRunning.get()) return
        }
        android.util.Log.e("EventUploader", "WebSocket failed ($serverUrl): ${t.message}", t)
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

    /**
     * Called when the OS reports a network reconnect (e.g. Wi-Fi re-associated),
     * bypassing the exponential backoff to re-establish the connection immediately.
     */
    fun triggerFastReconnect() {
        if (isRunning.get() && connectionState != ConnectionState.CONNECTED && connectionState != ConnectionState.CONNECTING) {
            reconnectJob?.cancel()
            reconnectJob = scope.launch {
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
                    lastPingSentMs = System.currentTimeMillis()
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
            android.util.Log.w("EventUploader", "sendFrame backpressure drop: queue=${ws.queueSize()} > $maxQueueBytes")
            return false
        }
        val jsonPayload = try {
            ContractsJson.encodeToString(FrameEvent.serializer(), frame)
        } catch (t: Throwable) {
            android.util.Log.e("EventUploader", "sendFrame json serialization error: ${t.message}", t)
            return false
        }
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

    /** T3.1: tell the server what this station currently allows and is actually doing. Sent after any
     *  change to the local cap and on connect, so the dashboard's mode is never a guess. */
    fun sendModeState(cap: String, effective: String, reason: String): Boolean {
        if (connectionState != ConnectionState.CONNECTED) return false
        val escaped = reason.replace("\\", "\\\\").replace("\"", "\\\"")
        return sendEnvelope("mode_state", """{"cap":"$cap","effective":"$effective","reason":"$escaped"}""")
    }

    /** T3.4: station requests to disconnect all active remote viewers immediately. */
    fun sendKickViewers(): Boolean {
        if (connectionState != ConnectionState.CONNECTED) return false
        val json = buildJsonObject {
            put("type", "kick_viewers")
        }.toString()
        val ws = synchronized(lock) { webSocket } ?: return false
        return try {
            ws.send(json)
        } catch (_: Exception) {
            false
        }
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

        previewsOffered.incrementAndGet()
        // Backpressure: drop preview first if queue > maxQueueBytes
        if (ws.queueSize() > maxQueueBytes) {
            android.util.Log.w("EventUploader", "sendPreview backpressure drop: queue=${ws.queueSize()} > $maxQueueBytes")
            previewDroppedBackpressure.incrementAndGet()
            return false
        }

        lastPreviewMs = nowMs
        val msg = ByteArray(1 + jpeg.size)
        msg[0] = KIND_PREVIEW
        System.arraycopy(jpeg, 0, msg, 1, jpeg.size)
        val sent = ws.send(msg.toByteString())
        if (!sent) {
            android.util.Log.w("EventUploader", "ws.send preview failed, queue=${ws.queueSize()}")
        }
        return sent
    }

    fun sendTelemetry(json: String): Boolean {
        if (connectionState != ConnectionState.CONNECTED) return false
        val ws = webSocket ?: return false
        if (ws.queueSize() > maxQueueBytes) return false
        return ws.send(json)
    }

    private fun sendEnvelope(type: String, jsonPayload: String): Boolean {
        val ws = webSocket ?: return false
        val envelope = """{"type":"$type","data":$jsonPayload}"""
        val sent = ws.send(envelope)
        if (!sent) {
            android.util.Log.w("EventUploader", "ws.send failed for $type, queue=${ws.queueSize()}")
        }
        return sent
    }
}
