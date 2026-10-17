package com.btb.ondevice.net

import java.util.Base64
import android.util.Log
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException
import java.util.concurrent.TimeUnit

/** What a successful POST /pair returned. */
data class PairedStation(val stationId: String, val householdId: String, val serverUrl: String)

sealed interface PairResult {
    data class Ok(val pairing: PairedStation) : PairResult
    data object InvalidCode : PairResult
    /** The server link is one this build may not use (H2); nothing was sent. */
    data class Refused(val reason: String) : PairResult
    data class Failed(val detail: String) : PairResult
}

/**
 * Registers this station with a household (T2.5): POST /pair carries the one-time code, the device
 * name and the Keystore public key. The code is the credential, so nothing else is sent.
 */
object StationPairing {
    private const val TAG = "Pairing"
    private val JSON = "application/json".toMediaType()

    private val client = OkHttpClient.Builder()
        .connectTimeout(5, TimeUnit.SECONDS)
        .readTimeout(10, TimeUnit.SECONDS)
        .build()

    /**
     * @param baseHttpUrl the dashboard origin (`https://host` or `http://host:8000`), not the ws URL
     * @param code the one-time code from the QR
     */
    /**
     * The endpoint for a pairing code. The QR carries the full `pair_url` (`http://host:8000/pair`), and an
     * origin (`http://host:8000`) is accepted too; the path is never doubled.
     */
    fun pairEndpoint(urlOrOrigin: String): String {
        val trimmed = urlOrOrigin.trimEnd('/')
        return if (trimmed.endsWith("/pair")) trimmed else "$trimmed/pair"
    }

    suspend fun pair(
        baseHttpUrl: String,
        code: String,
        deviceName: String,
        signer: Signer,
        allowCleartext: Boolean,
    ): PairResult {
        // Defence in depth: the pair_url is an HTTP call, so the same link rule applies (H2).
        val check = UrlPolicy.check(baseHttpUrl, allowCleartext)
        if (check is UrlCheck.Rejected) return PairResult.Refused(check.reason)
        return pairRequest(baseHttpUrl, code, deviceName, signer)
    }

    private suspend fun pairRequest(baseHttpUrl: String, code: String, deviceName: String, signer: Signer): PairResult =
        withContext(Dispatchers.IO) {
            val body = buildJsonObject {
                put("code", code)
                put("pubkey_spki_b64", signer.publicSpkiB64())
                put("device_name", deviceName)
            }.toString()
            val request = Request.Builder()
                .url(pairEndpoint(baseHttpUrl))
                .post(body.toRequestBody(JSON))
                .build()
            try {
                client.newCall(request).execute().use { res ->
                    when (res.code) {
                        200 -> {
                            val obj = Json.parseToJsonElement(res.body?.string().orEmpty()).jsonObject
                            val stationId = obj["station_id"]?.jsonPrimitive?.content
                            val householdId = obj["household_id"]?.jsonPrimitive?.content
                            if (stationId.isNullOrBlank() || householdId.isNullOrBlank()) {
                                return@use PairResult.InvalidCode
                            }
                            PairResult.Ok(PairedStation(stationId, householdId, baseHttpUrl))
                        }
                        400 -> PairResult.InvalidCode
                        else -> PairResult.Failed("HTTP ${res.code}")
                    }
                }
            } catch (err: IOException) {
                Log.w(TAG, "pair failed: ${err.message}")
                PairResult.Failed(err.message ?: "network error")
            }
        }

    /** Debug/test helper: the exact challenge message the server expects. */
    fun authMessage(nonceB64: String, stationId: String, device: String): ByteArray =
        "btb-auth-v1|".toByteArray() + Base64.getDecoder().decode(nonceB64) +
            "|$stationId|$device".toByteArray()

    /** Signs and wraps the challenge answer as the JSON envelope the server reads. */
    fun authEnvelope(nonceB64: String, stationId: String, device: String, signer: Signer): String {
        val sig = Base64.getEncoder().encodeToString(signer.sign(authMessage(nonceB64, stationId, device)))
        return buildJsonObject {
            put("type", "auth")
            put("sig", sig)
        }.toString()
    }

    /** Parses a challenge envelope, or null when it is something else. */
    fun parseChallenge(text: String): JsonObject? = try {
        val obj = Json.parseToJsonElement(text).jsonObject
        if (obj["type"]?.jsonPrimitive?.content == "challenge") obj else null
    } catch (_: Exception) {
        null
    }
}
