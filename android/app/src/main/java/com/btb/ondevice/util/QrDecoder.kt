package com.btb.ondevice.util

import com.btb.ondevice.net.UrlCheck
import com.btb.ondevice.net.UrlPolicy
import com.google.zxing.BinaryBitmap
import com.google.zxing.RGBLuminanceSource
import com.google.zxing.common.HybridBinarizer
import com.google.zxing.qrcode.QRCodeReader
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

data class PairingConfig(
    val url: String,
    val token: String? = null,
    val deviceName: String? = null,
)

/**
 * What a scanned QR means (T2.5). v1 JSON and plain URLs are manual configuration; v2 JSON carries a
 * one-time pairing code that buys the station a signed identity (POST /pair).
 */
sealed interface PairingTarget {
    data class Manual(val config: PairingConfig) : PairingTarget
    data class Code(val pairUrl: String, val wsUrl: String, val code: String) : PairingTarget

    /** A payload that names a server link this build may not use (H2). Carries the user-facing reason. */
    data class Rejected(val reason: String) : PairingTarget
}

object QrDecoder {
    private val qrReader = QRCodeReader()

    /**
     * Attempts to decode a QR code from an RGBA_8888 byte array.
     */
    fun decodeRgba(rgba: ByteArray, width: Int, height: Int): String? {
        if (rgba.size < width * height * 4) return null
        return try {
            val pixels = IntArray(width * height)
            var src = 0
            for (i in 0 until width * height) {
                val r = rgba[src].toInt() and 0xFF
                val g = rgba[src + 1].toInt() and 0xFF
                val b = rgba[src + 2].toInt() and 0xFF
                // Pack as 0xAARRGGBB
                pixels[i] = (0xFF shl 24) or (r shl 16) or (g shl 8) or b
                src += 4
            }

            val source = RGBLuminanceSource(width, height, pixels)
            val bitmap = BinaryBitmap(HybridBinarizer(source))
            val result = qrReader.decode(bitmap)
            result.text
        } catch (_: Exception) {
            null
        }
    }

    /**
     * Parses the raw QR code text into a structured PairingTarget.
     * Supports:
     * 1. JSON payload: {"v": 1, "url": "wss://...", "token": "..."}
     * 2. Direct URL: "wss://..." or "ws://" or "https://" or "http://"
     *
     * Every server link is checked as the user typed it (before normalizeUrl rewrites the scheme) with
     * [UrlPolicy]: a link the build may not use yields [PairingTarget.Rejected], never a saved config.
     * [allowCleartext] comes from the caller (production: BuildConfig.DEBUG); this object never reads it.
     */
    fun parsePairingText(rawText: String, allowCleartext: Boolean): PairingTarget? {
        val trimmed = rawText.trim()
        if (trimmed.isEmpty()) return null

        // Try JSON format
        if (trimmed.startsWith("{") && trimmed.endsWith("}")) {
            return try {
                val obj = Json.parseToJsonElement(trimmed).jsonObject
                if (obj["v"]?.jsonPrimitive?.content?.toIntOrNull() == 2) {
                    val code = obj["code"]?.jsonPrimitive?.content?.takeIf { it.isNotBlank() } ?: return null
                    val wsUrl = obj["url"]?.jsonPrimitive?.content ?: return null
                    val pairUrl = obj["pair_url"]?.jsonPrimitive?.content ?: return null
                    rejectUnless(allowCleartext, wsUrl, pairUrl)?.let { return it }
                    return PairingTarget.Code(pairUrl.trim(), normalizeUrl(wsUrl), code)
                }
                val url = obj["url"]?.jsonPrimitive?.content ?: return null
                rejectUnless(allowCleartext, url)?.let { return it }
                val token = obj["token"]?.jsonPrimitive?.content?.takeIf { it.isNotBlank() }
                val device = obj["device"]?.jsonPrimitive?.content?.takeIf { it.isNotBlank() }
                PairingTarget.Manual(PairingConfig(normalizeUrl(url), token, device))
            } catch (_: Exception) {
                null
            }
        }

        // Try direct URL format
        if (trimmed.startsWith("ws://") || trimmed.startsWith("wss://") ||
            trimmed.startsWith("http://") || trimmed.startsWith("https://")
        ) {
            rejectUnless(allowCleartext, trimmed)?.let { return it }
            return PairingTarget.Manual(PairingConfig(normalizeUrl(trimmed)))
        }

        return null
    }

    /** The first rejected link among [urls], or null when all of them may be used. */
    private fun rejectUnless(allowCleartext: Boolean, vararg urls: String): PairingTarget.Rejected? {
        for (url in urls) {
            val check = UrlPolicy.check(url, allowCleartext)
            if (check is UrlCheck.Rejected) return PairingTarget.Rejected(check.reason)
        }
        return null
    }

    private fun normalizeUrl(rawUrl: String): String {
        var url = rawUrl.trim()
        // If HTTP(S), convert to WS(S)
        if (url.startsWith("http://")) {
            url = "ws://" + url.removePrefix("http://")
        } else if (url.startsWith("https://")) {
            url = "wss://" + url.removePrefix("https://")
        }

        // Ensure path ends with /ingest-events if root or /ingest
        if (!url.contains("/ingest-events")) {
            url = if (url.endsWith("/")) {
                url + "ingest-events"
            } else if (url.endsWith("/ingest")) {
                url + "-events"
            } else {
                "$url/ingest-events"
            }
        }
        return url
    }
}
