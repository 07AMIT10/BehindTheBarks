package com.btb.ondevice.util

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
     * Parses the raw QR code text into a structured PairingConfig.
     * Supports:
     * 1. JSON payload: {"v": 1, "url": "wss://...", "token": "..."}
     * 2. Direct URL: "wss://..." or "ws://" or "https://" or "http://"
     */
    fun parsePairingText(rawText: String): PairingConfig? {
        val trimmed = rawText.trim()
        if (trimmed.isEmpty()) return null

        // Try JSON format
        if (trimmed.startsWith("{") && trimmed.endsWith("}")) {
            return try {
                val obj = Json.parseToJsonElement(trimmed).jsonObject
                val url = obj["url"]?.jsonPrimitive?.content ?: return null
                val token = obj["token"]?.jsonPrimitive?.content?.takeIf { it.isNotBlank() }
                val device = obj["device"]?.jsonPrimitive?.content?.takeIf { it.isNotBlank() }
                PairingConfig(normalizeUrl(url), token, device)
            } catch (_: Exception) {
                null
            }
        }

        // Try direct URL format
        if (trimmed.startsWith("ws://") || trimmed.startsWith("wss://") ||
            trimmed.startsWith("http://") || trimmed.startsWith("https://")
        ) {
            return PairingConfig(normalizeUrl(trimmed))
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
