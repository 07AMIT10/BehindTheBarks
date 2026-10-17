package com.btb.ondevice.ui

/**
 * A wagwatch:// link is attacker-controllable (any web page can fire one). Its token may replace the
 * stored token only when the link points at the same host as the server already configured.
 * Kept free of Android types so the rule is unit-tested on the JVM.
 */
internal object DeepLinkPolicy {
    fun hostOf(url: String?): String? {
        if (url.isNullOrBlank()) return null
        return runCatching { java.net.URI(url.trim()).host }.getOrNull()?.lowercase()
    }

    /** True only when the configured server and the link's server are the same host. */
    fun mayApplyToken(currentUrl: String?, linkUrl: String?): Boolean {
        val current = hostOf(currentUrl) ?: return false
        return current == hostOf(linkUrl)
    }
}
