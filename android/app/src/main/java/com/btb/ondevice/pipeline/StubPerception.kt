package com.btb.ondevice.pipeline

import com.btb.ondevice.contracts.AudioEvent
import com.btb.ondevice.contracts.AudioLabel
import com.btb.ondevice.contracts.EMOTIONS
import com.btb.ondevice.contracts.EarPosition
import com.btb.ondevice.contracts.Emotion
import com.btb.ondevice.contracts.Features
import com.btb.ondevice.contracts.FrameEvent
import com.btb.ondevice.contracts.RulesLabel
import java.io.ByteArrayOutputStream
import kotlin.math.sin

/**
 * Scripted synthetic perception stack for end-to-end testing and Milestone M1.
 *
 * Cycles through standard emotional phases:
 *  - relaxed (12s)
 *  - excited (12s)
 *  - happy (18s)
 *  - disinterested (14s)
 *  - anxious (10s)
 *  - absent / no dog (6s)
 *  - relaxed (18s)
 *
 * Total cycle = 90s, matching backend/demo/mock_pipeline.py.
 */
class StubPerception(
    val clock: () -> Double = { System.currentTimeMillis() / 1000.0 },
) {
    companion object {
        val PHASES = listOf(
            "relaxed" to 12.0,
            "excited" to 12.0,
            "happy" to 18.0,
            "disinterested" to 14.0,
            "anxious" to 10.0,
            "absent" to 6.0,
            "relaxed" to 18.0,
        )
        val LOOP_S = PHASES.sumOf { it.second }

        /** Minimal valid 1x1 JPEG bytes for preview streaming tests. */
        val TINY_JPEG: ByteArray = byteArrayOf(
            0xFF.toByte(), 0xD8.toByte(), 0xFF.toByte(), 0xE0.toByte(), 0x00.toByte(), 0x10.toByte(),
            0x4A.toByte(), 0x46.toByte(), 0x49.toByte(), 0x46.toByte(), 0x00.toByte(), 0x01.toByte(),
            0x01.toByte(), 0x00.toByte(), 0x00.toByte(), 0x01.toByte(), 0x00.toByte(), 0x01.toByte(),
            0x00.toByte(), 0x00.toByte(), 0xFF.toByte(), 0xDB.toByte(), 0x00.toByte(), 0x43.toByte(),
            0x00.toByte(), 0x08.toByte(), 0x06.toByte(), 0x06.toByte(), 0x07.toByte(), 0x06.toByte(),
            0x05.toByte(), 0x08.toByte(), 0x07.toByte(), 0x07.toByte(), 0x07.toByte(), 0x09.toByte(),
            0x09.toByte(), 0x08.toByte(), 0x0A.toByte(), 0x0C.toByte(), 0x14.toByte(), 0x0D.toByte(),
            0x0C.toByte(), 0x0B.toByte(), 0x0B.toByte(), 0x0C.toByte(), 0x19.toByte(), 0x12.toByte(),
            0x13.toByte(), 0x0F.toByte(), 0x14.toByte(), 0x1D.toByte(), 0x1A.toByte(), 0x1F.toByte(),
            0x1E.toByte(), 0x1D.toByte(), 0x1A.toByte(), 0x1C.toByte(), 0x1C.toByte(), 0x20.toByte(),
            0x24.toByte(), 0x2E.toByte(), 0x27.toByte(), 0x20.toByte(), 0x22.toByte(), 0x2C.toByte(),
            0x23.toByte(), 0x1C.toByte(), 0x1C.toByte(), 0x28.toByte(), 0x37.toByte(), 0x29.toByte(),
            0x2C.toByte(), 0x30.toByte(), 0x31.toByte(), 0x34.toByte(), 0x34.toByte(), 0x34.toByte(),
            0x1F.toByte(), 0x27.toByte(), 0x39.toByte(), 0x3D.toByte(), 0x38.toByte(), 0x32.toByte(),
            0x3C.toByte(), 0x2E.toByte(), 0x33.toByte(), 0x34.toByte(), 0x32.toByte(), 0xFF.toByte(),
            0xC0.toByte(), 0x00.toByte(), 0x0B.toByte(), 0x08.toByte(), 0x00.toByte(), 0x01.toByte(),
            0x00.toByte(), 0x01.toByte(), 0x01.toByte(), 0x01.toByte(), 0x11.toByte(), 0x00.toByte(),
            0xFF.toByte(), 0xC4.toByte(), 0x00.toByte(), 0x1F.toByte(), 0x00.toByte(), 0x00.toByte(),
            0x01.toByte(), 0x05.toByte(), 0x01.toByte(), 0x01.toByte(), 0x01.toByte(), 0x01.toByte(),
            0x01.toByte(), 0x01.toByte(), 0x00.toByte(), 0x00.toByte(), 0x00.toByte(), 0x00.toByte(),
            0x00.toByte(), 0x00.toByte(), 0x00.toByte(), 0x00.toByte(), 0x01.toByte(), 0x02.toByte(),
            0x03.toByte(), 0x04.toByte(), 0x05.toByte(), 0x06.toByte(), 0x07.toByte(), 0x08.toByte(),
            0x09.toByte(), 0x0A.toByte(), 0x0B.toByte(), 0xFF.toByte(), 0xDA.toByte(), 0x00.toByte(),
            0x08.toByte(), 0x01.toByte(), 0x01.toByte(), 0x00.toByte(), 0x00.toByte(), 0x3F.toByte(),
            0x00.toByte(), 0xBF.toByte(), 0x80.toByte(), 0xFF.toByte(), 0xD9.toByte()
        )
    }

    private var t0 = clock()
    private var lastAudioTs = 0.0

    fun reset(now: Double = clock()) {
        t0 = now
        lastAudioTs = 0.0
    }

    fun currentPhase(now: Double = clock()): String {
        val rel = (now - t0) % LOOP_S
        var cumulative = 0.0
        for ((name, dur) in PHASES) {
            cumulative += dur
            if (rel < cumulative) {
                return name
            }
        }
        return "relaxed"
    }

    data class StepOutput(
        val frame: FrameEvent,
        val rules: RulesLabel,
        val audio: AudioEvent? = null,
        val previewJpeg: ByteArray? = null,
    )

    fun step(now: Double = clock()): StepOutput {
        val phase = currentPhase(now)
        val dogDetected = phase != "absent"

        val features = when (phase) {
            "relaxed" -> Features(
                tailHeight = 0.0,
                tailWagHz = 0.5,
                earPosition = EarPosition.neutral,
                mouthOpen = 0.3,
                bodyLowering = 0.05,
                motionEnergy = 0.08,
                inFeedingZone = true,
            )
            "excited" -> Features(
                tailHeight = 0.7,
                tailWagHz = 4.5,
                earPosition = EarPosition.up,
                mouthOpen = 0.7,
                bodyLowering = 0.0,
                motionEnergy = 0.85,
                inFeedingZone = true,
            )
            "happy" -> Features(
                tailHeight = 0.3,
                tailWagHz = 2.0,
                earPosition = EarPosition.neutral,
                mouthOpen = 0.55,
                bodyLowering = 0.2,
                motionEnergy = 0.35,
                inFeedingZone = true,
            )
            "disinterested" -> Features(
                tailHeight = -0.1,
                tailWagHz = 0.0,
                earPosition = EarPosition.neutral,
                mouthOpen = 0.1,
                bodyLowering = 0.1,
                motionEnergy = 0.03,
                inFeedingZone = true,
            )
            "anxious" -> Features(
                tailHeight = -0.45,
                tailWagHz = 0.3,
                earPosition = EarPosition.back,
                mouthOpen = 0.2,
                bodyLowering = 0.35,
                motionEnergy = 0.7,
                inFeedingZone = false,
            )
            else -> Features()
        }

        val bbox = if (dogDetected) listOf(120.0, 80.0, 520.0, 400.0) else null
        val bboxConf = if (dogDetected) 0.92 else null

        val frame = FrameEvent(
            ts = now,
            source = "stub",
            dogDetected = dogDetected,
            bbox = bbox,
            bboxConf = bboxConf,
            features = features,
        )

        val scores = mutableMapOf<Emotion, Double>()
        for (e in EMOTIONS) {
            scores[e] = 0.0
        }
        val emotion = if (!dogDetected) {
            Emotion.unknown
        } else {
            Emotion.valueOf(phase)
        }
        scores[emotion] = 0.85
        scores[Emotion.unknown] = if (!dogDetected) 1.0 else 0.1

        val rules = RulesLabel(
            ts = now,
            emotion = emotion,
            confidence = 0.75,
            scores = scores,
        )

        var audio: AudioEvent? = null
        if (phase == "excited" && (now - lastAudioTs) >= 2.0) {
            audio = AudioEvent(ts = now, label = AudioLabel.yip, score = 0.8)
            lastAudioTs = now
        } else if (phase == "anxious" && (now - lastAudioTs) >= 3.0) {
            audio = AudioEvent(ts = now, label = AudioLabel.whimper, score = 0.75)
            lastAudioTs = now
        }

        return StepOutput(
            frame = frame,
            rules = rules,
            audio = audio,
            previewJpeg = TINY_JPEG,
        )
    }
}
