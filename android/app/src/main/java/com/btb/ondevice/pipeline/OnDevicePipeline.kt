package com.btb.ondevice.pipeline

import com.btb.ondevice.audio.AudioPipeline
import com.btb.ondevice.config.DataConfig
import com.btb.ondevice.contracts.AudioEvent
import com.btb.ondevice.contracts.Features
import com.btb.ondevice.contracts.FrameEvent
import com.btb.ondevice.contracts.RulesLabel
import com.btb.ondevice.fusion.RulesEngine
import com.btb.ondevice.vision.Box
import com.btb.ondevice.vision.BoxTracker
import com.btb.ondevice.vision.DogDetector
import com.btb.ondevice.vision.FaceLandmarker
import com.btb.ondevice.vision.FeatureExtractor
import com.btb.ondevice.vision.KeypointMap
import com.btb.ondevice.vision.PoseEstimator
import com.btb.ondevice.vision.RoiWagEstimator
import com.btb.ondevice.vision.boxIou
import com.btb.ondevice.vision.pointInZone
import java.nio.ByteBuffer
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt

interface PipelineListener {
    fun onFrameEvent(event: FrameEvent)
    fun onRulesLabel(label: RulesLabel)
    fun onAudioEvent(event: AudioEvent)
}

class OnDevicePipeline(
    val config: DataConfig,
    val tierZeroGate: TierZeroGate = TierZeroGate(),
    val dogDetector: DogDetector = DogDetector(),
    val boxTracker: BoxTracker = BoxTracker(
        alpha = config.detect.emaAlpha,
        resetAfterS = config.detect.emaResetS,
    ),
    val poseEstimator: PoseEstimator = PoseEstimator(),
    val faceLandmarker: FaceLandmarker = FaceLandmarker(),
    val wagEstimator: RoiWagEstimator = RoiWagEstimator(),
    val featureExtractor: FeatureExtractor = FeatureExtractor(config),
    val rulesEngine: RulesEngine = RulesEngine(config),
    val audioPipeline: AudioPipeline = AudioPipeline(),
    val scheduler: Scheduler = Scheduler(),
    val executor: InferenceExecutor = InferenceExecutor(),
    var listener: PipelineListener? = null,
    val sourceName: String = "ondevice",
) : AutoCloseable {

    private var lastKeypoints: Map<String, KeypointMap.Point?> = emptyMap()
    private var lastFaceLandmarks: List<List<Double>>? = null
    private var lastHeadBoxValid = false
    private var lastDetectionConf: Double? = null
    private var lastTrackTs: Double = -1.0
    private var lastMotionTs: Double = 0.0
    private var lastInnovationIoU: Double = 1.0

    private val heldAudioEvents = mutableListOf<AudioEvent>()
    private val heldTreatTimestamps = mutableListOf<Double>()

    fun pushTreat(ts: Double) {
        synchronized(heldTreatTimestamps) {
            heldTreatTimestamps.add(ts)
        }
    }

    fun pushAudioPcm16(ts: Double, pcm: ShortArray): List<AudioEvent> {
        val events = audioPipeline.pushPcm16(ts, pcm)
        if (events.isNotEmpty()) {
            synchronized(heldAudioEvents) {
                heldAudioEvents.addAll(events)
            }
            events.forEach { listener?.onAudioEvent(it) }
        }
        return events
    }

    /**
     * Feed pre-decoded fixture components directly (replay mode for testing and validation).
     */
    fun processReplayFrame(
        ts: Double,
        bbox: List<Double>?,
        bboxConf: Double?,
        bodyKeypoints: Map<String, List<Double>?>,
        faceLandmarks: List<List<Double>>?,
        audioEvents: List<AudioEvent> = emptyList(),
        isTreatRecent: Boolean = false,
        wagHz: Double? = null,
        inFeedingZone: Boolean = false,
        precomputedFeatures: Features? = null,
    ): Pair<FrameEvent, RulesLabel?> {
        val frameEvent = if (precomputedFeatures != null) {
            FrameEvent(
                ts = ts,
                source = sourceName,
                dogDetected = bbox != null && bbox.size >= 4,
                bbox = bbox,
                bboxConf = bboxConf,
                bodyKeypoints = bodyKeypoints,
                faceLandmarks = faceLandmarks,
                features = precomputedFeatures,
            )
        } else {
            featureExtractor.update(
                ts = ts,
                bbox = bbox,
                bboxConf = bboxConf,
                inFeedingZone = inFeedingZone,
                kps = bodyKeypoints,
                lms = faceLandmarks,
                roiWagHz = wagHz,
            )
        }

        val rulesLabel = if (frameEvent.dogDetected) {
            rulesEngine.update(
                frame = frameEvent,
                audioEvents = audioEvents,
                treatEventRecent = isTreatRecent,
            )
        } else null

        listener?.onFrameEvent(frameEvent)
        if (rulesLabel != null) {
            listener?.onRulesLabel(rulesLabel)
        }

        return frameEvent to rulesLabel
    }

    /**
     * Process a camera frame in RGBA format through the on-device perception pipeline.
     */
    fun processFrame(
        ts: Double,
        width: Int,
        height: Int,
        rgba: ByteBuffer,
    ): Pair<FrameEvent, RulesLabel?> {
        // 1. Tier-0 motion gate
        val lumDownsample = downsampleRgba(rgba, width, height, 32, 32)
        val frameDiff = tierZeroGate.computeFrameDiff(lumDownsample)
        val motionDetected = frameDiff >= tierZeroGate.diffThreshold
        if (motionDetected) {
            lastMotionTs = ts
        }

        // 2. ROI Wag estimator (every frame)
        val currentBox = boxTracker.current()
        val wagHz = if (currentBox != null) {
            val grayRoi = extractTailRoi(rgba, width, height, currentBox, lastKeypoints, lastTrackTs, ts)
            if (grayRoi != null) {
                wagEstimator.push(grayRoi, ts)
            } else null
        } else null

        // 3. Scheduler
        val headBoxValid = lastHeadBoxValid
        val trackState = TrackState(
            hasTrack = currentBox != null,
            lastTrackTs = lastTrackTs,
            innovationIoU = lastInnovationIoU,
        )
        val tick = scheduler.onFrame(
            ts = ts,
            gate = GateState(
                motionDetected = motionDetected,
                dogLikelyPresent = currentBox != null,
                lastMotionTs = lastMotionTs,
            ),
            track = trackState,
            headBoxValid = headBoxValid,
        )

        // 4. Detection
        if (tick.detect) {
            val det = dogDetector.detect(rgba, width, height)
            if (det != null) {
                val prev = boxTracker.current()
                val smoothed = boxTracker.update(det.box, ts)
                if (prev != null && smoothed != null) {
                    lastInnovationIoU = boxIou(prev, det.box)
                }
                lastTrackTs = ts
                lastDetectionConf = det.score.toDouble()
            } else {
                boxTracker.update(null, ts)
                if (boxTracker.current() == null) {
                    lastKeypoints = emptyMap()
                    lastFaceLandmarks = null
                    lastHeadBoxValid = false
                }
            }
        }

        val activeBox = boxTracker.current()
        val dogDetected = activeBox != null

        // 5. Pose
        if (dogDetected && tick.pose) {
            val kps = poseEstimator.estimate(rgba, width, height, activeBox!!)
            lastKeypoints = kps
            val hBox = faceLandmarker.headBox(kps)
            lastHeadBoxValid = hBox != null
        }

        // 6. Face
        if (dogDetected && tick.face && lastHeadBoxValid) {
            val lms = faceLandmarker.estimate(rgba, width, height, lastKeypoints)
            if (lms != null) {
                lastFaceLandmarks = lms
            }
        }

        // 7. Features & Zone
        val kpsForFeatures = lastKeypoints.mapValues { (_, pt) ->
            if (pt != null) listOf(pt.x, pt.y, pt.conf) else null
        }
        val inZone = if (activeBox != null && config.feedingZone.isNotEmpty()) {
            val anchorX = (activeBox.x1 + activeBox.x2) / 2.0
            val anchorY = activeBox.y2
            pointInZone(anchorX, anchorY, config.feedingZone, width.toDouble(), height.toDouble())
        } else false

        val frameEvent = featureExtractor.update(
            ts = ts,
            bbox = activeBox?.toList(),
            bboxConf = if (dogDetected) lastDetectionConf else null,
            inFeedingZone = inZone,
            kps = kpsForFeatures,
            lms = lastFaceLandmarks,
            roiWagHz = wagHz,
        )

        // 8. Rules
        val recentAudio: List<AudioEvent>
        synchronized(heldAudioEvents) {
            recentAudio = heldAudioEvents.filter { (ts - it.ts) in 0.0..config.audio.maxGapS }
        }
        val recentTreats: List<Double>
        synchronized(heldTreatTimestamps) {
            recentTreats = heldTreatTimestamps.filter { (ts - it) in 0.0..config.rules.treatWindowS }
        }

        val rulesLabel = if (dogDetected) {
            rulesEngine.update(
                frame = frameEvent,
                audioEvents = recentAudio,
                treatEventRecent = recentTreats.isNotEmpty(),
            )
        } else null

        listener?.onFrameEvent(frameEvent)
        if (rulesLabel != null) {
            listener?.onRulesLabel(rulesLabel)
        }

        return frameEvent to rulesLabel
    }

    /**
     * Downsample RGBA buffer to grayscale byte array of size dstW * dstH.
     */
    fun downsampleRgba(
        rgba: ByteBuffer,
        srcW: Int,
        srcH: Int,
        dstW: Int = 32,
        dstH: Int = 32,
    ): ByteArray {
        val out = ByteArray(dstW * dstH)
        val stepX = srcW.toDouble() / dstW.toDouble()
        val stepY = srcH.toDouble() / dstH.toDouble()

        for (y in 0 until dstH) {
            val srcY = (y * stepY).toInt().coerceIn(0, srcH - 1)
            val rowOffset = y * dstW
            for (x in 0 until dstW) {
                val srcX = (x * stepX).toInt().coerceIn(0, srcW - 1)
                val pixelIdx = (srcY * srcW + srcX) * 4
                val r = rgba.get(pixelIdx).toInt() and 0xFF
                val g = rgba.get(pixelIdx + 1).toInt() and 0xFF
                val b = rgba.get(pixelIdx + 2).toInt() and 0xFF
                val gray = (0.299 * r + 0.587 * g + 0.114 * b).toInt().coerceIn(0, 255)
                out[rowOffset + x] = gray.toByte()
            }
        }
        return out
    }

    /**
     * Extract a 64x64 grayscale tail ROI from the full-frame RGBA buffer.
     */
    fun extractTailRoi(
        rgba: ByteBuffer,
        frameW: Int,
        frameH: Int,
        bbox: Box,
        kps: Map<String, KeypointMap.Point?>,
        lastPoseTs: Double,
        ts: Double,
    ): Array<ByteArray>? {
        val bw = max(1.0, bbox.width)
        val bh = max(1.0, bbox.height)

        val tailBase = kps["tail_base"]
        val poseFresh = tailBase != null && (lastPoseTs < 0 || (ts - lastPoseTs) <= 1.0)

        val rx1: Int
        val ry1: Int
        val rw: Int
        val rh: Int

        if (poseFresh && tailBase != null) {
            val dim = max(32, (0.35 * max(bw, bh)).toInt())
            val half = dim / 2
            rx1 = max(0, (tailBase.x - half).roundToInt())
            ry1 = max(0, (tailBase.y - half).roundToInt())
            rw = min(frameW - rx1, dim)
            rh = min(frameH - ry1, dim)
        } else {
            val rearFrac = 0.35
            val rwCalc = max(16, (bw * rearFrac).toInt())
            rx1 = max(0, (bbox.x2 - rwCalc).roundToInt())
            ry1 = max(0, bbox.y1.roundToInt())
            rw = min(frameW - rx1, rwCalc)
            rh = min(frameH - ry1, bh.toInt())
        }

        if (rw < 8 || rh < 8) return null

        val roi = Array(64) { ByteArray(64) }
        val stepX = rw.toDouble() / 64.0
        val stepY = rh.toDouble() / 64.0

        for (y in 0 until 64) {
            val srcY = (ry1 + y * stepY).toInt().coerceIn(0, frameH - 1)
            for (x in 0 until 64) {
                val srcX = (rx1 + x * stepX).toInt().coerceIn(0, frameW - 1)
                val pixelIdx = (srcY * frameW + srcX) * 4
                val r = rgba.get(pixelIdx).toInt() and 0xFF
                val g = rgba.get(pixelIdx + 1).toInt() and 0xFF
                val b = rgba.get(pixelIdx + 2).toInt() and 0xFF
                val gray = (0.299 * r + 0.587 * g + 0.114 * b).toInt().coerceIn(0, 255)
                roi[y][x] = gray.toByte()
            }
        }

        return roi
    }

    override fun close() {
        executor.close()
    }
}
