package com.btb.ondevice.pipeline

enum class DegradeLevel {
    L0_NORMAL,
    L1_WARM,
    L2_HOT,
    L3_CRITICAL,
}

data class Cadence(
    val detectHz: Double,
    val detectHzNoTrack: Double = 3.0,
    val poseHz: Double,
    val faceHz: Double,
    val audioMaxHz: Double = 2.0,
) {
    companion object {
        val L0 = Cadence(detectHz = 3.0, detectHzNoTrack = 3.0, poseHz = 6.0, faceHz = 1.0, audioMaxHz = 2.0)
        val L1 = Cadence(detectHz = 2.0, detectHzNoTrack = 2.0, poseHz = 5.0, faceHz = 0.5, audioMaxHz = 2.0)
        val L2 = Cadence(detectHz = 1.0, detectHzNoTrack = 1.0, poseHz = 4.0, faceHz = 0.0, audioMaxHz = 1.0)
        val L3 = Cadence(detectHz = 1.0, detectHzNoTrack = 1.0, poseHz = 0.0, faceHz = 0.0, audioMaxHz = 1.0)

        fun forLevel(level: DegradeLevel): Cadence = when (level) {
            DegradeLevel.L0_NORMAL -> L0
            DegradeLevel.L1_WARM -> L1
            DegradeLevel.L2_HOT -> L2
            DegradeLevel.L3_CRITICAL -> L3
        }
    }
}

data class Tick(
    val detect: Boolean,
    val pose: Boolean,
    val face: Boolean,
)

data class TrackState(
    val hasTrack: Boolean,
    val lastTrackTs: Double,
    val innovationIoU: Double = 1.0,
)

data class GateState(
    val motionDetected: Boolean,
    val dogLikelyPresent: Boolean,
    val lastMotionTs: Double,
)

class Scheduler(
    private var cadence: Cadence = Cadence.L0,
    private val clock: () -> Double = { System.currentTimeMillis() / 1000.0 },
) {
    private var lastDetectTs = -1.0
    private var lastPoseTs = -1.0
    private var lastFaceTs = -1.0
    private var currentLevel = DegradeLevel.L0_NORMAL
    private var wasIdle = false

    fun setLevel(level: DegradeLevel) {
        currentLevel = level
        cadence = Cadence.forLevel(level)
    }

    fun getLevel(): DegradeLevel = currentLevel
    fun getCadence(): Cadence = cadence

    fun onFrame(ts: Double, gate: GateState, track: TrackState, headBoxValid: Boolean): Tick {
        // No dog and no motion for 10 s -> detect drops to 1 Hz ("idle watch") and pose and face stop.
        val idleWatch = !track.hasTrack && !gate.motionDetected && (ts - gate.lastMotionTs >= 10.0)
        val motionJustReturned = wasIdle && gate.motionDetected
        wasIdle = idleWatch

        val targetDetectHz = if (idleWatch) {
            1.0
        } else if (!track.hasTrack) {
            cadence.detectHzNoTrack
        } else {
            cadence.detectHz
        }

        val detectInterval = if (targetDetectHz > 0) 1.0 / targetDetectHz else Double.MAX_VALUE
        val timeSinceDetect = if (lastDetectTs < 0) Double.MAX_VALUE else (ts - lastDetectTs)
        val trackStale = timeSinceDetect >= (detectInterval - 1e-4)
        val innovationLarge = track.hasTrack && (track.innovationIoU < 0.5)

        val shouldDetect = (targetDetectHz > 0) && (trackStale || innovationLarge || motionJustReturned || (idleWatch && timeSinceDetect >= 1.0))
        if (shouldDetect) {
            lastDetectTs = ts
        }

        // Pose rules: never exceeds poseHz, never runs without a track younger than 0.5s, stops during idleWatch
        val trackAge = if (track.hasTrack) ts - track.lastTrackTs else Double.MAX_VALUE
        val poseInterval = if (cadence.poseHz > 0) 1.0 / cadence.poseHz else Double.MAX_VALUE
        val timeSincePose = if (lastPoseTs < 0) Double.MAX_VALUE else (ts - lastPoseTs)

        val shouldPose = !idleWatch &&
                cadence.poseHz > 0 &&
                track.hasTrack &&
                trackAge <= 0.5 &&
                timeSincePose >= (poseInterval - 1e-4)

        if (shouldPose) {
            lastPoseTs = ts
        }

        // Face rules: runs only if headBoxValid, not idle, faceHz > 0, interval elapsed
        val faceInterval = if (cadence.faceHz > 0) 1.0 / cadence.faceHz else Double.MAX_VALUE
        val timeSinceFace = if (lastFaceTs < 0) Double.MAX_VALUE else (ts - lastFaceTs)

        val shouldFace = !idleWatch &&
                headBoxValid &&
                cadence.faceHz > 0 &&
                timeSinceFace >= (faceInterval - 1e-4)

        if (shouldFace) {
            lastFaceTs = ts
        }

        return Tick(detect = shouldDetect, pose = shouldPose, face = shouldFace)
    }

    fun reset() {
        lastDetectTs = -1.0
        lastPoseTs = -1.0
        lastFaceTs = -1.0
        wasIdle = false
    }
}
