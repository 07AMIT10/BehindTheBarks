package com.btb.ondevice.audio

import com.btb.ondevice.capture.ViewMode

/**
 * T3.9 / A2: may the phone stream live microphone audio right now? Pure, so the rule is unit-tested
 * without Android. Audio belongs to Full mode only (docs/superpowers/plans/2026-10-11-audio-and-hardening
 * decision 1): Limited, Privacy and Blackout never carry sound, and nobody is listening means no sound.
 *
 * Evaluate it for every chunk, not once: a drop to a lower mode must stop audio on the very next chunk.
 */
object AudioStreamGate {
    fun shouldStream(effective: ViewMode, policyAudio: Boolean, audioViewers: Int): Boolean =
        effective == ViewMode.FULL && policyAudio && audioViewers > 0
}
