package com.btb.ondevice.net

import java.net.URI

/** The verdict on one server link. */
sealed interface UrlCheck {
    data object Allowed : UrlCheck
    data class Rejected(val reason: String) : UrlCheck
}

/**
 * Which server links the app may use (H2). Pure Kotlin so the rule is unit-tested on the JVM; the caller
 * passes `allowCleartext` (production: `BuildConfig.DEBUG`), never this file reading BuildConfig.
 *
 * - `allowCleartext = false` (release): only `wss://` and `https://` with a real host.
 * - `allowCleartext = true` (debug): also `ws://` and `http://` for the adb-reverse and LAN dev loop.
 * - Any other scheme, an empty string, or a link without a host is rejected in both modes.
 *
 * The host is whatever `java.net.URI` says it is, so `wss://good.com@evil.com` is judged as `evil.com`.
 */
object UrlPolicy {
    private val SECURE = setOf("wss", "https")
    private val CLEARTEXT = setOf("ws", "http")

    /** The host the policy judges, lowercased, or null when the link has none (or does not parse). */
    fun hostOf(url: String): String? =
        runCatching { URI(url.trim()).host }.getOrNull()?.lowercase()

    fun check(url: String, allowCleartext: Boolean): UrlCheck {
        val trimmed = url.trim()
        if (trimmed.isEmpty()) return reject("empty")
        val uri = try {
            URI(trimmed)
        } catch (_: Exception) {
            return reject("not a valid URL")
        }
        val scheme = uri.scheme?.lowercase() ?: return reject("no scheme")
        val schemeAllowed = scheme in SECURE || (allowCleartext && scheme in CLEARTEXT)
        if (!schemeAllowed) {
            return if (scheme in CLEARTEXT) insecure(scheme) else reject(scheme)
        }
        if (uri.host.isNullOrBlank()) return reject("$scheme with no host")
        return UrlCheck.Allowed
    }

    private fun insecure(scheme: String) =
        UrlCheck.Rejected("Insecure server link ($scheme://). This build only connects over wss://.")

    private fun reject(what: String) =
        UrlCheck.Rejected("Unsupported server link ($what). This build only connects over wss://.")
}
