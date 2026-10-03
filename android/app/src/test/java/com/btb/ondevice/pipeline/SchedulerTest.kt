package com.btb.ondevice.pipeline

import com.google.common.truth.Truth.assertThat
import org.junit.jupiter.api.Test

class SchedulerTest {

    @Test
    fun `idle watch triggers after 10s of no dog and no motion`() {
        val scheduler = Scheduler()
        val initialTs = 100.0

        // Motion was 10s ago, no current motion, no track
        val idleGate = GateState(motionDetected = false, dogLikelyPresent = false, lastMotionTs = initialTs - 10.0)
        val noTrack = TrackState(hasTrack = false, lastTrackTs = 0.0)

        // Frame at initialTs: should detect (first detect)
        val tick1 = scheduler.onFrame(initialTs, idleGate, noTrack, headBoxValid = false)
        assertThat(tick1.detect).isTrue()
        assertThat(tick1.pose).isFalse()
        assertThat(tick1.face).isFalse()

        // 0.33s later (normal 3 Hz interval): in idle watch (1 Hz), detect should NOT fire
        val tick2 = scheduler.onFrame(initialTs + 0.33, idleGate, noTrack, headBoxValid = false)
        assertThat(tick2.detect).isFalse()

        // 1.0s later: idle watch detect fires
        val tick3 = scheduler.onFrame(initialTs + 1.0, idleGate, noTrack, headBoxValid = false)
        assertThat(tick3.detect).isTrue()

        // Motion returns -> detects immediately on the next frame at detectHzNoTrack
        val motionGate = GateState(motionDetected = true, dogLikelyPresent = false, lastMotionTs = initialTs + 1.2)
        val tick4 = scheduler.onFrame(initialTs + 1.2, motionGate, noTrack, headBoxValid = false)
        assertThat(tick4.detect).isTrue()
    }

    @Test
    fun `large innovation triggers immediate detection`() {
        val scheduler = Scheduler()
        val ts = 50.0
        val gate = GateState(motionDetected = true, dogLikelyPresent = true, lastMotionTs = ts)

        // Stable track
        val track = TrackState(hasTrack = true, lastTrackTs = ts, innovationIoU = 0.9)
        scheduler.onFrame(ts, gate, track, headBoxValid = false)

        // 0.05s later (much faster than 3 Hz / 0.33s): normal frame does not detect
        val normalTick = scheduler.onFrame(ts + 0.05, gate, track, headBoxValid = false)
        assertThat(normalTick.detect).isFalse()

        // Jump in box -> innovation IoU drops to 0.2 < 0.5 -> triggers immediate detect!
        val jumpTrack = TrackState(hasTrack = true, lastTrackTs = ts, innovationIoU = 0.2)
        val jumpTick = scheduler.onFrame(ts + 0.06, gate, jumpTrack, headBoxValid = false)
        assertThat(jumpTick.detect).isTrue()
    }

    @Test
    fun `pose runs only with fresh track and respects poseHz`() {
        val scheduler = Scheduler()
        var ts = 10.0
        val gate = GateState(motionDetected = true, dogLikelyPresent = true, lastMotionTs = ts)

        // Track is 0.8s old (> 0.5s limit) -> pose is suppressed
        val staleTrack = TrackState(hasTrack = true, lastTrackTs = ts - 0.8)
        val staleTick = scheduler.onFrame(ts, gate, staleTrack, headBoxValid = false)
        assertThat(staleTick.pose).isFalse()

        // Fresh track (0.1s old) -> pose runs
        val freshTrack = TrackState(hasTrack = true, lastTrackTs = ts - 0.1)
        val freshTick = scheduler.onFrame(ts, gate, freshTrack, headBoxValid = false)
        assertThat(freshTick.pose).isTrue()

        // 0.05s later: too fast for 6 Hz (interval 0.166s) -> pose suppressed
        val fastTick = scheduler.onFrame(ts + 0.05, gate, freshTrack, headBoxValid = false)
        assertThat(fastTick.pose).isFalse()

        // 0.17s later: 6 Hz interval reached -> pose runs
        val nextTick = scheduler.onFrame(ts + 0.17, gate, freshTrack, headBoxValid = false)
        assertThat(nextTick.pose).isTrue()
    }

    @Test
    fun `degrade levels throttle cadence according to thermal ladder`() {
        val scheduler = Scheduler()
        assertThat(scheduler.getCadence().poseHz).isEqualTo(6.0)
        assertThat(scheduler.getCadence().faceHz).isEqualTo(1.0)

        // Level L2 (hot): face is turned off (0.0), pose is 4 Hz
        scheduler.setLevel(DegradeLevel.L2_HOT)
        assertThat(scheduler.getCadence().poseHz).isEqualTo(4.0)
        assertThat(scheduler.getCadence().faceHz).isEqualTo(0.0)

        // Level L3 (critical): pose and face turned off (0.0), detect is 1 Hz
        scheduler.setLevel(DegradeLevel.L3_CRITICAL)
        assertThat(scheduler.getCadence().detectHz).isEqualTo(1.0)
        assertThat(scheduler.getCadence().poseHz).isEqualTo(0.0)
        assertThat(scheduler.getCadence().faceHz).isEqualTo(0.0)
    }
}
