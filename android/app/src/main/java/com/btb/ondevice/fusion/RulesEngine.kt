// Port of backend/fusion/rules.py
// Commit: eaec4db617a69a740218fdba88cfcaee9b9e50dc
package com.btb.ondevice.fusion

import com.btb.ondevice.config.DataConfig
import com.btb.ondevice.contracts.AudioEvent
import com.btb.ondevice.contracts.EMOTIONS
import com.btb.ondevice.contracts.EarPosition
import com.btb.ondevice.contracts.Emotion
import com.btb.ondevice.contracts.Features
import com.btb.ondevice.contracts.FrameEvent
import com.btb.ondevice.contracts.RulesLabel
import java.math.BigDecimal
import java.math.RoundingMode
import kotlin.math.max
import kotlin.math.min

object RulesMath {
    fun clamp01(x: Double): Double = max(0.0, min(1.0, x))

    fun above(x: Double?, thr: Double, soft: Double): Double? =
        if (x == null) null else clamp01(0.5 + (x - thr) / (2.0 * soft))

    fun below(x: Double?, thr: Double, soft: Double): Double? =
        if (x == null) null else clamp01(0.5 - (x - thr) / (2.0 * soft))

    fun between(x: Double?, lo: Double, hi: Double, soft: Double): Double? {
        if (x == null) return null
        val a = above(x, lo, soft) ?: return null
        val b = below(x, hi, soft) ?: return null
        return min(a, b)
    }

    fun round4(x: Double): Double =
        BigDecimal.valueOf(x).setScale(4, RoundingMode.HALF_EVEN).toDouble()

    fun ears(ear: EarPosition?, want: String): Double? {
        if (ear == null || ear == EarPosition.unknown) return null
        return when (want) {
            "up" -> when (ear) {
                EarPosition.up -> 1.0
                EarPosition.neutral -> 0.3
                EarPosition.back -> 0.0
                else -> null
            }
            "neutral" -> when (ear) {
                EarPosition.up -> 0.6
                EarPosition.neutral -> 1.0
                EarPosition.back -> 0.0
                else -> null
            }
            "back" -> when (ear) {
                EarPosition.up -> 0.0
                EarPosition.neutral -> 0.0
                EarPosition.back -> 1.0
                else -> null
            }
            else -> null
        }
    }
}

class RulesEngine(val config: DataConfig = DataConfig()) {
    private data class View(
        val f: Features,
        val sounds: Map<String, Double>,
        val idleS: Double,
    )

    private var current: String? = null
    private var candidate: String? = null
    private var candidateSince = 0.0
    private var lastDogTs: Double? = null
    private var lastLabel: RulesLabel? = null
    private var idleStart: Double? = null
    private var idleOkTs = 0.0

    private val rulesCfg get() = config.rules
    private val t get() = rulesCfg.thresholds
    private val soft get() = rulesCfg.softness

    fun reset() {
        current = null
        candidate = null
        candidateSince = 0.0
        lastDogTs = null
        lastLabel = null
        idleStart = null
        idleOkTs = 0.0
    }

    fun update(
        frame: FrameEvent,
        audioEvents: List<AudioEvent> = emptyList(),
        treatEventRecent: Boolean = false,
    ): RulesLabel {
        val ts = frame.ts
        val idleS = updateIdle(frame, treatEventRecent)
        if (!frame.dogDetected) {
            return noDog(ts)
        }
        lastDogTs = ts

        val sounds = recentSounds(ts, audioEvents).toMutableMap()
        if (treatEventRecent) {
            sounds["treat"] = 1.0
        }

        var feat = frame.features
        if (rulesCfg.ignoreEatingConfounds && feat.inFeedingZone == true) {
            if (feat.bodyLowering != null && feat.bodyLowering!! > 0.05) {
                feat = feat.copy(mouthOpen = null, bodyLowering = null)
            }
        }
        val view = View(feat, sounds, idleS)

        val emotionNames = listOf("happy", "excited", "relaxed", "anxious", "fearful", "aggressive", "disinterested")
        val scores = mutableMapOf<String, Double>()
        for (e in emotionNames) {
            val rawScore = when (e) {
                "happy" -> scoreHappy(view)
                "excited" -> scoreExcited(view)
                "relaxed" -> scoreRelaxed(view)
                "anxious" -> scoreAnxious(view)
                "fearful" -> scoreFearful(view)
                "aggressive" -> scoreAggressive(view)
                "disinterested" -> scoreDisinterested(view)
                else -> 0.0
            }
            scores[e] = RulesMath.clamp01(rawScore)
        }
        scores["unknown"] = rulesCfg.minScore

        // Dict order tie-breaking: first key with max score wins
        var bestEmotion = emotionNames.first()
        var maxScore = -1.0
        val allOrder = emotionNames + listOf("unknown")
        for (e in allOrder) {
            val sc = scores[e] ?: 0.0
            if (sc > maxScore) {
                maxScore = sc
                bestEmotion = e
            }
        }

        val resolved = applyHysteresis(bestEmotion, ts)
        return emit(ts, resolved, scores)
    }

    private fun recentSounds(ts: Double, audioEvents: List<AudioEvent>): Map<String, Double> {
        val sounds = mutableMapOf<String, Double>()
        val winS = rulesCfg.windowS
        for (e in audioEvents) {
            if (e.ts > ts - winS) {
                val label = e.label.name
                sounds[label] = max(sounds[label] ?: 0.0, e.score)
            }
        }
        return sounds
    }

    private fun noDog(ts: Double): RulesLabel {
        val gap = if (lastDogTs == null) Double.POSITIVE_INFINITY else ts - lastDogTs!!
        if (gap <= rulesCfg.noDogUnknownS && lastLabel != null) {
            val held = lastLabel!!.copy(ts = ts)
            lastLabel = held
            return held
        }
        val scores = mutableMapOf<String, Double>()
        for (e in EMOTIONS) {
            scores[e.name] = 0.0
        }
        scores["unknown"] = 1.0
        current = "unknown"
        candidate = null
        return emit(ts, "unknown", scores)
    }

    private fun applyHysteresis(raw: String, ts: Double): String {
        if (current == null) {
            current = raw
        } else if (raw == current) {
            candidate = null
        } else {
            if (raw != candidate) {
                candidate = raw
                candidateSince = ts
            }
            if (ts - candidateSince >= rulesCfg.hysteresisS) {
                current = raw
                candidate = null
            }
        }
        return current!!
    }

    private fun emit(ts: Double, emotionStr: String, scores: Map<String, Double>): RulesLabel {
        val runnerUp = scores.entries
            .filter { it.key != emotionStr }
            .maxOfOrNull { it.value } ?: 0.0

        val emotionScore = scores[emotionStr] ?: 0.0
        val confidence = RulesMath.clamp01(emotionScore - 0.5 * runnerUp)

        val scoresMap = mutableMapOf<Emotion, Double>()
        for (e in EMOTIONS) {
            val sc = scores[e.name] ?: 0.0
            scoresMap[e] = RulesMath.round4(sc)
        }

        val label = RulesLabel(
            ts = ts,
            emotion = Emotion.valueOf(emotionStr),
            confidence = confidence,
            scores = scoresMap,
        )
        lastLabel = label
        return label
    }

    private fun updateIdle(frame: FrameEvent, treat: Boolean): Double {
        val ts = frame.ts
        val f = frame.features
        val motionLow = t["motion_low"] ?: 0.035
        val ok = frame.dogDetected && !treat && f.inFeedingZone == true &&
            f.motionEnergy != null && f.motionEnergy!! < motionLow

        if (treat) {
            idleStart = null
        } else if (ok) {
            if (idleStart == null) {
                idleStart = ts
            }
            idleOkTs = ts
        } else if (idleStart != null && (ts - idleOkTs) > rulesCfg.disinterestedGraceS) {
            idleStart = null
        }

        if (idleStart == null || (ts - idleOkTs) > rulesCfg.disinterestedGraceS) {
            return 0.0
        }
        return ts - idleStart!!
    }

    private fun blend(emotion: String, evidence: Map<String, Double?>): Double {
        val w = rulesCfg.weights[emotion] ?: return 0.0
        var total = 0.0
        for ((k, _) in evidence) {
            total += w[k] ?: 0.0
        }
        val present = evidence.filterValues { it != null }
        var presentW = 0.0
        for ((k, _) in present) {
            presentW += w[k] ?: 0.0
        }
        if (total <= 0.0 || presentW <= 0.0) return 0.0

        var mean = 0.0
        for ((k, v) in present) {
            mean += (w[k] ?: 0.0) * v!!
        }
        mean /= presentW

        val minCoverage = rulesCfg.minCoverage
        return mean * min(1.0, (presentW / total) / minCoverage)
    }

    private fun adjust(emotion: String, base: Double, v: View): Double {
        val bonuses = rulesCfg.eventBonus[emotion] ?: emptyMap()
        var score = base
        for ((sig, b) in bonuses) {
            score += b * (v.sounds[sig] ?: 0.0)
        }
        val penalties = rulesCfg.eventPenalty[emotion] ?: emptyMap()
        for ((sig, pen) in penalties) {
            score *= (1.0 - pen * (v.sounds[sig] ?: 0.0))
        }
        return RulesMath.clamp01(score)
    }

    private fun scoreHappy(v: View): Double {
        val f = v.f
        val base = blend("happy", mapOf(
            "tail_ok" to RulesMath.above(f.tailHeight, t["tail_low"] ?: -0.3, soft["tail_height"] ?: 0.2),
            "wag_moderate" to RulesMath.between(f.tailWagHz, t["wag_moderate_min_hz"] ?: 1.0, t["wag_fast_hz"] ?: 3.0, soft["wag_hz"] ?: 1.0),
            "mouth_open" to RulesMath.above(f.mouthOpen, t["mouth_open"] ?: 0.4, soft["mouth_open"] ?: 0.15),
            "ears_neutral" to RulesMath.ears(f.earPosition, "neutral"),
            "not_lowered" to RulesMath.below(f.bodyLowering, t["body_lowering_mild"] ?: 0.2, soft["body_lowering"] ?: 0.1),
            "motion_moderate" to RulesMath.between(f.motionEnergy, t["motion_low"] ?: 0.035, t["motion_high"] ?: 0.8, soft["motion"] ?: 0.15),
        ))
        return adjust("happy", base, v)
    }

    private fun scoreExcited(v: View): Double {
        val f = v.f
        val base = blend("excited", mapOf(
            "wag_fast" to RulesMath.above(f.tailWagHz, t["wag_fast_hz"] ?: 3.0, soft["wag_hz"] ?: 1.0),
            "tail_high" to RulesMath.above(f.tailHeight, t["tail_high"] ?: 0.3, soft["tail_height"] ?: 0.2),
            "motion_high" to RulesMath.above(f.motionEnergy, t["motion_high"] ?: 0.8, soft["motion"] ?: 0.15),
            "ears_up" to RulesMath.ears(f.earPosition, "up"),
            "mouth_open" to RulesMath.above(f.mouthOpen, t["mouth_open"] ?: 0.4, soft["mouth_open"] ?: 0.15),
        ))
        return adjust("excited", base, v)
    }

    private fun scoreRelaxed(v: View): Double {
        val f = v.f
        val base = blend("relaxed", mapOf(
            "motion_low" to RulesMath.below(f.motionEnergy, t["motion_low"] ?: 0.035, soft["motion"] ?: 0.15),
            "tail_neutral" to RulesMath.between(f.tailHeight, t["tail_low"] ?: -0.3, t["tail_high"] ?: 0.3, soft["tail_height"] ?: 0.2),
            "wag_slow" to RulesMath.below(f.tailWagHz, t["wag_moderate_min_hz"] ?: 1.0, soft["wag_hz"] ?: 1.0),
            "ears_neutral" to RulesMath.ears(f.earPosition, "neutral"),
            "not_lowered" to RulesMath.below(f.bodyLowering, t["body_lowering_mild"] ?: 0.2, soft["body_lowering"] ?: 0.1),
        ))
        return adjust("relaxed", base * (1.0 - scoreDisinterested(v)), v)
    }

    private fun scoreAnxious(v: View): Double {
        val f = v.f
        val pacing = if (f.inFeedingZone != null) {
            if (f.inFeedingZone == true) 0.0 else RulesMath.above(f.motionEnergy, t["motion_high"] ?: 0.8, soft["motion"] ?: 0.15)
        } else null

        val base = blend("anxious", mapOf(
            "tail_low" to RulesMath.between(f.tailHeight, t["tail_tucked"] ?: -0.6, t["tail_low"] ?: -0.3, soft["tail_height"] ?: 0.2),
            "ears_back" to RulesMath.ears(f.earPosition, "back"),
            "lowering_some" to RulesMath.between(f.bodyLowering, t["body_lowering_mild"] ?: 0.2, t["body_lowering_high"] ?: 0.5, soft["body_lowering"] ?: 0.1),
            "pacing" to pacing,
        ))
        return adjust("anxious", base, v)
    }

    private fun scoreFearful(v: View): Double {
        val f = v.f
        val away = if (f.inFeedingZone != null) {
            if (f.inFeedingZone == true) 0.0 else 1.0
        } else null

        val base = blend("fearful", mapOf(
            "tail_tucked" to RulesMath.below(f.tailHeight, t["tail_tucked"] ?: -0.6, soft["tail_height"] ?: 0.2),
            "ears_back" to RulesMath.ears(f.earPosition, "back"),
            "lowering_high" to RulesMath.above(f.bodyLowering, t["body_lowering_high"] ?: 0.5, soft["body_lowering"] ?: 0.1),
            "away_from_zone" to away,
        ))
        return adjust("fearful", base, v)
    }

    private fun scoreAggressive(v: View): Double {
        val f = v.f
        val base = blend("aggressive", mapOf(
            "still_tail" to RulesMath.below(f.tailWagHz, t["wag_moderate_min_hz"] ?: 1.0, soft["wag_hz"] ?: 1.0),
            "motion_low" to RulesMath.below(f.motionEnergy, t["motion_low"] ?: 0.035, soft["motion"] ?: 0.15),
            "ears_up" to RulesMath.ears(f.earPosition, "up"),
            "tail_high" to RulesMath.above(f.tailHeight, t["tail_high"] ?: 0.3, soft["tail_height"] ?: 0.2),
            "mouth_open" to RulesMath.above(f.mouthOpen, t["mouth_open"] ?: 0.4, soft["mouth_open"] ?: 0.15),
        ))
        var score = adjust("aggressive", base, v)
        val growlScore = v.sounds["growl"] ?: 0.0
        val growlMin = rulesCfg.growlMinScore
        if (rulesCfg.suppressAggressiveWithoutGrowl && growlScore < growlMin) {
            return 0.0
        }
        if (growlScore < growlMin) {
            score = min(score, rulesCfg.aggressiveMaxConfWithoutGrowl)
        }
        return score
    }

    private fun scoreDisinterested(v: View): Double {
        val thr = rulesCfg.disinterestedS
        if (v.idleS < thr) return 0.0
        val lo = rulesCfg.disinterestedMinScore
        return lo + (1.0 - lo) * min(1.0, (v.idleS - thr) / thr)
    }
}
