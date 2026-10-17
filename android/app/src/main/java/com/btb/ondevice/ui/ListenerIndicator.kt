package com.btb.ondevice.ui

/**
 * T3.9 anti-lurking rules for live microphone listeners. Pure, so the Android code only shows what these
 * return and the rules are unit-tested without Android.
 */
object ListenerIndicator {
    /** "N listening" for the notification and the row, or null when nobody listens (the row is hidden). */
    fun countText(listeners: Int): String? = if (listeners > 0) "$listeners listening" else null

    /** The on-screen row: a headphone symbol and [countText]. */
    fun rowText(listeners: Int): String? = countText(listeners)?.let { "🎧 $it" }

    /** Spoken description for TalkBack, with the same count. */
    fun description(listeners: Int): String =
        if (listeners == 1) "1 person is listening to the microphone"
        else "$listeners people are listening to the microphone"

    /** The listener chime plays once when the first listener arrives (0 to more than 0), never for N to M. */
    fun chimesOn(previous: Int, next: Int): Boolean = previous <= 0 && next > 0
}
