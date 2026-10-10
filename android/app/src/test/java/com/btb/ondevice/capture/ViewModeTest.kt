package com.btb.ondevice.capture

import com.google.common.truth.Truth.assertThat
import org.junit.jupiter.api.Test

class ViewModeTest {

    private fun controller(
        cap: ViewMode = ViewMode.FULL,
        now: () -> Long,
        allowOwnerRaise: Boolean = false,
    ) = ModeController(cap, now, allowOwnerRaise)

    @Test
    fun `ordering is blackout privacy limited full`() {
        assertThat(ViewMode.BLACKOUT.rank).isLessThan(ViewMode.PRIVACY.rank)
        assertThat(ViewMode.PRIVACY.rank).isLessThan(ViewMode.LIMITED.rank)
        assertThat(ViewMode.LIMITED.rank).isLessThan(ViewMode.FULL.rank)
    }

    @Test
    fun `min picks the less exposed`() {
        assertThat(ViewMode.FULL.minOf(ViewMode.PRIVACY)).isEqualTo(ViewMode.PRIVACY)
        assertThat(ViewMode.PRIVACY.minOf(ViewMode.FULL)).isEqualTo(ViewMode.PRIVACY)
        assertThat(ViewMode.LIMITED.minOf(ViewMode.LIMITED)).isEqualTo(ViewMode.LIMITED)
    }

    @Test
    fun `wire values parse and unknown values return null`() {
        assertThat(ViewMode.fromWire("full")).isEqualTo(ViewMode.FULL)
        assertThat(ViewMode.fromWire("BLACKOUT")).isEqualTo(ViewMode.BLACKOUT)
        assertThat(ViewMode.fromWire(" limited ")).isEqualTo(ViewMode.LIMITED)
        assertThat(ViewMode.fromWire("ultra")).isNull()
        assertThat(ViewMode.fromWire(null)).isNull()
    }

    @Test
    fun `effective mode is the minimum of cap and policy`() {
        var now = 0L
        val c = controller(cap = ViewMode.LIMITED, now = { now })
        c.onPolicy(Policy(mode = ViewMode.FULL))
        assertThat(c.effectiveMode()).isEqualTo(ViewMode.LIMITED) // cap lowers
        c.onPolicy(Policy(mode = ViewMode.PRIVACY))
        assertThat(c.effectiveMode()).isEqualTo(ViewMode.PRIVACY) // policy lowers
    }

    @Test
    fun `a remote mode request that lowers applies immediately`() {
        var now = 0L
        val c = controller(cap = ViewMode.FULL, now = { now })
        c.onPolicy(Policy(mode = ViewMode.FULL))
        val outcome = c.onModeRequest(ViewMode.PRIVACY, fromOwner = true)
        assertThat(outcome).isInstanceOf(ModeController.RequestOutcome.Applied::class.java)
        assertThat(c.effectiveMode()).isEqualTo(ViewMode.FULL) // the CAP is unchanged; policy still governs
    }

    @Test
    fun `a remote raise is denied without approval`() {
        val c = controller(cap = ViewMode.PRIVACY, now = { 0L })
        c.onPolicy(Policy(mode = ViewMode.PRIVACY))
        val outcome = c.onModeRequest(ViewMode.FULL, fromOwner = true)
        assertThat(outcome).isInstanceOf(ModeController.RequestOutcome.NeedsApproval::class.java)
        assertThat(c.effectiveMode()).isEqualTo(ViewMode.PRIVACY) // unchanged, not raised
    }

    @Test
    fun `an approved raise takes effect then expires after fifteen minutes`() {
        var now = 1_000L
        val c = controller(cap = ViewMode.PRIVACY, now = { now })
        c.onPolicy(Policy(mode = ViewMode.FULL))
        assertThat(c.effectiveMode()).isEqualTo(ViewMode.PRIVACY)
        c.approveRaise(ViewMode.FULL)
        assertThat(c.effectiveMode()).isEqualTo(ViewMode.FULL) // raised now
        now += 14 * 60_000
        assertThat(c.effectiveMode()).isEqualTo(ViewMode.FULL) // still within the window
        now += 2 * 60_000 // now past 15 minutes
        assertThat(c.effectiveMode()).isEqualTo(ViewMode.PRIVACY) // fell back to the cap
    }

    @Test
    fun `owner raise can skip the prompt only when explicitly allowed`() {
        val c = controller(cap = ViewMode.PRIVACY, now = { 0L }, allowOwnerRaise = true)
        c.onPolicy(Policy(mode = ViewMode.FULL))
        c.onModeRequest(ViewMode.FULL, fromOwner = true)
        assertThat(c.effectiveMode()).isEqualTo(ViewMode.FULL) // auto-approved
    }

    @Test
    fun `viewer raise is never auto approved even when owner raises can`() {
        val c = controller(cap = ViewMode.PRIVACY, now = { 0L }, allowOwnerRaise = true)
        c.onPolicy(Policy(mode = ViewMode.FULL))
        val outcome = c.onModeRequest(ViewMode.FULL, fromOwner = false)
        assertThat(outcome).isInstanceOf(ModeController.RequestOutcome.NeedsApproval::class.java)
        assertThat(c.effectiveMode()).isEqualTo(ViewMode.PRIVACY)
    }

    @Test
    fun `legacy privacy true is honoured before any policy (old servers)`() {
        val c = controller(cap = ViewMode.FULL, now = { 0L })
        assertThat(c.ignoresLegacyPrivacy()).isFalse()
        c.onLegacyPrivacy(true)
        assertThat(c.effectiveMode()).isEqualTo(ViewMode.BLACKOUT)
        assertThat(c.cap).isEqualTo(ViewMode.BLACKOUT)
    }

    @Test
    fun `legacy privacy true is ignored after a policy, so the station cap stays full`() {
        val c = controller(cap = ViewMode.FULL, now = { 0L })
        c.onPolicy(Policy(mode = ViewMode.FULL))
        assertThat(c.ignoresLegacyPrivacy()).isTrue()
        c.onLegacyPrivacy(true)
        assertThat(c.cap).isEqualTo(ViewMode.FULL)
        assertThat(c.effectiveMode()).isEqualTo(ViewMode.FULL)
    }

    @Test
    fun `remote blackout by policy, then remote full, needs no on-device approval`() {
        val c = controller(cap = ViewMode.FULL, now = { 0L })
        c.onPolicy(Policy(mode = ViewMode.BLACKOUT))
        c.onLegacyPrivacy(true)                 // the server also sends the legacy downlink
        assertThat(c.effectiveMode()).isEqualTo(ViewMode.BLACKOUT)
        c.onPolicy(Policy(mode = ViewMode.FULL)) // owner restores
        assertThat(c.effectiveMode()).isEqualTo(ViewMode.FULL)
        assertThat(c.cap).isEqualTo(ViewMode.FULL)
        // the owner's raise above the cap would have been "pending"; here it is a policy change
        assertThat(c.onModeRequest(ViewMode.FULL, fromOwner = true))
            .isInstanceOf(ModeController.RequestOutcome.Applied::class.java)
    }

    @Test
    fun `beginSession forgets the previous policy so legacy privacy is honoured again`() {
        val c = controller(cap = ViewMode.FULL, now = { 0L })
        c.onPolicy(Policy(mode = ViewMode.FULL))
        c.beginSession()
        assertThat(c.ignoresLegacyPrivacy()).isFalse()
        c.onLegacyPrivacy(true)
        assertThat(c.cap).isEqualTo(ViewMode.BLACKOUT)
    }

    @Test
    fun `legacy privacy false never raises exposure from a remote message`() {
        val c = controller(cap = ViewMode.PRIVACY, now = { 0L })
        c.onPolicy(Policy(mode = ViewMode.FULL))
        c.onLegacyPrivacy(false) // a remote "unmute" must be ignored
        assertThat(c.effectiveMode()).isEqualTo(ViewMode.PRIVACY)
    }

    @Test
    fun `the device UI is the only way to raise the cap`() {
        val c = controller(cap = ViewMode.PRIVACY, now = { 0L })
        c.onPolicy(Policy(mode = ViewMode.FULL))
        c.setStationCapFromDevice(ViewMode.FULL) // person on the phone changes it
        assertThat(c.effectiveMode()).isEqualTo(ViewMode.FULL)
    }

    @Test
    fun `previews are only encoded when someone is watching in limited or full`() {
        val c = controller(cap = ViewMode.FULL, now = { 0L })
        c.onPolicy(Policy(mode = ViewMode.FULL, videoViewers = 1))
        assertThat(c.shouldEncodePreview()).isTrue()
        c.onPolicy(Policy(mode = ViewMode.FULL, videoViewers = 0))
        assertThat(c.shouldEncodePreview()).isFalse() // nobody watching
        c.onPolicy(Policy(mode = ViewMode.PRIVACY, videoViewers = 2))
        assertThat(c.shouldEncodePreview()).isFalse() // privacy: no image at all
        c.onPolicy(Policy(mode = ViewMode.BLACKOUT, videoViewers = 2))
        assertThat(c.shouldEncodePreview()).isFalse()
        c.onPolicy(Policy(mode = ViewMode.LIMITED, videoViewers = 1))
        assertThat(c.shouldEncodePreview()).isTrue()
    }

    @Test
    fun `audio streams only in full with audio allowed and an audio viewer`() {
        val c = controller(cap = ViewMode.FULL, now = { 0L })
        c.onPolicy(Policy(mode = ViewMode.FULL, audio = true, audioViewers = 1))
        assertThat(c.shouldStreamAudio()).isTrue()
        c.onPolicy(Policy(mode = ViewMode.FULL, audio = true, audioViewers = 0))
        assertThat(c.shouldStreamAudio()).isFalse() // nobody listening
        c.onPolicy(Policy(mode = ViewMode.LIMITED, audio = true, audioViewers = 1))
        assertThat(c.shouldStreamAudio()).isFalse() // not full
    }

    @Test
    fun `preview interval comes from the policy fps`() {
        assertThat(Policy(previewFps = 2).previewIntervalMs()).isEqualTo(500L)
        assertThat(Policy(previewFps = 1).previewIntervalMs()).isEqualTo(1000L)
        assertThat(Policy(previewFps = 0).previewIntervalMs()).isEqualTo(Long.MAX_VALUE)
    }

    @Test
    fun `a raise that needs approval is pending, allowed, then raised with expiry`() {
        var now = 1_000L
        val c = controller(cap = ViewMode.PRIVACY, now = { now })
        c.onPolicy(Policy(mode = ViewMode.FULL))
        c.onModeRequest(ViewMode.FULL, fromOwner = true)
        assertThat(c.pendingRaise()?.mode).isEqualTo(ViewMode.FULL)
        assertThat(c.effectiveMode()).isEqualTo(ViewMode.PRIVACY)

        assertThat(c.allowPendingRaise()).isEqualTo(ViewMode.FULL)
        assertThat(c.pendingRaise()).isNull()
        assertThat(c.effectiveMode()).isEqualTo(ViewMode.FULL)
        assertThat(c.effectiveReason()).isEqualTo(ModeReason.APPROVED_RAISE)

        now += 15 * 60_000 // the approved raise lapses
        assertThat(c.effectiveMode()).isEqualTo(ViewMode.PRIVACY)
    }

    @Test
    fun `keep private drops the pending raise and leaves the cap alone`() {
        val c = controller(cap = ViewMode.PRIVACY, now = { 0L })
        c.onPolicy(Policy(mode = ViewMode.FULL))
        c.onModeRequest(ViewMode.FULL, fromOwner = true)
        assertThat(c.denyPendingRaise()).isTrue()
        assertThat(c.pendingRaise()).isNull()
        assertThat(c.allowPendingRaise()).isNull() // nothing left to allow
        assertThat(c.effectiveMode()).isEqualTo(ViewMode.PRIVACY)
        assertThat(c.cap).isEqualTo(ViewMode.PRIVACY)
    }

    @Test
    fun `an unanswered raise times out to deny after sixty seconds`() {
        var now = 0L
        val c = controller(cap = ViewMode.PRIVACY, now = { now })
        c.onPolicy(Policy(mode = ViewMode.FULL))
        c.onModeRequest(ViewMode.FULL, fromOwner = true)
        now += 59_000
        assertThat(c.pendingRaise()).isNotNull() // still waiting
        now += 2_000
        assertThat(c.pendingRaise()).isNull()     // default deny
        assertThat(c.allowPendingRaise()).isNull() // a late Allow does nothing
        assertThat(c.effectiveMode()).isEqualTo(ViewMode.PRIVACY)
    }

    @Test
    fun `a new session or a device cap change clears a pending raise`() {
        val c = controller(cap = ViewMode.PRIVACY, now = { 0L })
        c.onPolicy(Policy(mode = ViewMode.FULL))
        c.onModeRequest(ViewMode.FULL, fromOwner = true)
        c.beginSession()
        assertThat(c.pendingRaise()).isNull()
        c.onModeRequest(ViewMode.FULL, fromOwner = true)
        c.setStationCapFromDevice(ViewMode.LIMITED)
        assertThat(c.pendingRaise()).isNull()
    }

    @Test
    fun `an approved raise is reported as the raised cap until it lapses`() {
        var now = 0L
        val c = controller(cap = ViewMode.PRIVACY, now = { now })
        c.onPolicy(Policy(mode = ViewMode.FULL))
        assertThat(c.reportedCap()).isEqualTo(ViewMode.PRIVACY)
        c.approveRaise(ViewMode.FULL)
        assertThat(c.reportedCap()).isEqualTo(ViewMode.FULL)
        assertThat(c.cap).isEqualTo(ViewMode.PRIVACY) // the device's own setting is untouched
        now += 15 * 60_000
        assertThat(c.reportedCap()).isEqualTo(ViewMode.PRIVACY)
    }

    @Test
    fun `the approval question names the requested mode`() {
        assertThat(approvalQuestion(ViewMode.FULL))
            .isEqualTo("A remote viewer asks to raise sharing to Full. Allow for 15 minutes?")
        assertThat(approvalQuestion(ViewMode.LIMITED)).contains("to raise sharing to Limited.")
        assertThat(approvalQuestion(ViewMode.PRIVACY)).contains("to raise sharing to Privacy.")
    }
}
