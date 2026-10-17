package com.btb.ondevice.capture

import com.btb.ondevice.audio.AudioStreamGate

/**
 * T3.1/T3.2 viewing modes, ordered by how much may leave the phone.
 *
 * The phone is the authority: whatever the dashboard asks for, the effective mode is
 * `min(stationCap, policy.mode)`. Raising the cap can only happen on the device.
 */
enum class ViewMode(val wire: String, val rank: Int) {
    BLACKOUT("blackout", 0),
    PRIVACY("privacy", 1),
    LIMITED("limited", 2),
    FULL("full", 3);

    companion object {
        /** Parse a wire value. An unknown value is never guessed: it falls back to [PRIVACY],
         *  the safest mode that still lets barks through, and returns null so the caller can log it. */
        fun fromWire(value: String?): ViewMode? = when (value?.trim()?.lowercase()) {
            "blackout" -> BLACKOUT
            "privacy" -> PRIVACY
            "limited" -> LIMITED
            "full" -> FULL
            else -> null
        }
    }

    /** The less exposed of the two. This is the ceiling that applies. */
    fun minOf(other: ViewMode): ViewMode = if (this.rank <= other.rank) this else other
}

/** Configurable UI descriptor for viewing modes. Allows reordering and future extensibility. */
data class ModeConfig(
    val mode: ViewMode,
    val title: String,
    val subtitle: String,
    val icon: String,
    val enabled: Boolean = true,
)

object ViewModeRegistry {
    /**
     * Central configurable mode list. Order defines UI display sequence.
     * FULL is first/recommended for dog camera monitoring.
     */
    val DISPLAY_OPTIONS: List<ModeConfig> = listOf(
        ModeConfig(
            mode = ViewMode.FULL,
            title = "Full",
            subtitle = "Live video & perception (Recommended)",
            icon = "👁️",
        ),
        ModeConfig(
            mode = ViewMode.LIMITED,
            title = "Limited",
            subtitle = "Blurred snapshots & privacy",
            icon = "🖼️",
        ),
        ModeConfig(
            mode = ViewMode.PRIVACY,
            title = "Privacy",
            // I1: Privacy is truly bark/behaviour-only - barks are numbers, but no room audio
            // (nor image) is recorded or uploaded, snippets included.
            subtitle = "Barks and behaviour only (No video, no audio)",
            icon = "🔇",
        ),
        ModeConfig(
            mode = ViewMode.BLACKOUT,
            title = "Blackout",
            subtitle = "Nothing leaves the phone",
            icon = "⛔",
        ),
    )

    fun activeOptions(): List<ModeConfig> = DISPLAY_OPTIONS.filter { it.enabled }
}

/** A server-sent viewing policy (T3.1 `policy` downlink). */
data class Policy(
    val mode: ViewMode = ViewMode.FULL,
    val previewFps: Int = 2,
    val previewMaxEdge: Int = 320,
    val previewQuality: Int = 60,
    val audio: Boolean = false,
    val videoViewers: Int = 0,
    val audioViewers: Int = 0,
) {
    companion object {
        val DEFAULT = Policy()

        /** A policy that reveals nothing: used when no server has spoken yet. */
        val SAFEST = Policy(mode = ViewMode.BLACKOUT, videoViewers = 0, audioViewers = 0)
    }

    /** Preview interval in ms, from the policy's fps (T3.2: the phone no longer hard-codes 500 ms). */
    fun previewIntervalMs(): Long = if (previewFps <= 0) Long.MAX_VALUE else 1000L / previewFps
}

/** Why the effective mode is what it is; reported upstream in `mode_state`. */
enum class ModeReason(val wire: String) {
    STATION("station"),
    POLICY("policy"),
    APPROVED_RAISE("approved_raise"),
    EXPIRED_RAISE("expired_raise"),
}

/** How a raise prompt ended. The wire value goes upstream in `mode_result` (T3.2 / server envelope). */
enum class RaiseResult(val wire: String) {
    ALLOWED("allowed"),
    DENIED("denied"),
    TIMEOUT("timeout"),
}

/** The raise that was answered, and how. */
data class RaiseResolution(val mode: ViewMode, val result: RaiseResult)

/**
 * T3.2 the station's mode decisions, extracted from [MonitorService] so they are testable without
 * Android. Every rule about who may raise or lower exposure lives here, not in the service.
 */
class ModeController(
    private var stationCap: ViewMode = ViewMode.FULL,
    private val clock: () -> Long = { System.currentTimeMillis() },
    /** When true, an owner raise tagged `from=owner` skips the prompt. Off by default (T3.2). */
    private val allowOwnerRaiseWithoutPrompt: Boolean = false,
) {
    /** How long an approved raise lasts before the phone falls back to its own cap. */
    var raiseDurationMs: Long = 15 * 60_000L

    /** How long an unanswered raise prompt waits before it is denied by default. */
    var requestTimeoutMs: Long = 60_000L

    /** A raise the owner asked for and the person on the phone has not answered yet. */
    data class PendingRaise(val mode: ViewMode, val deadline: Long)

    private var pending: PendingRaise? = null

    /** The unanswered raise, or null. A prompt past its deadline is denied and cleared here. */
    @Synchronized
    fun pendingRaise(): PendingRaise? {
        val p = pending ?: return null
        if (clock() >= p.deadline) {
            pending = null
            return null
        }
        return p
    }

    /**
     * Ends the pending raise with the on-device answer. [RaiseResult.ALLOWED] approves it for [raiseDurationMs];
     * [RaiseResult.DENIED] and [RaiseResult.TIMEOUT] leave the cap alone. A prompt that is past its deadline
     * is reported as a timeout whatever the caller says, so a late tap cannot approve an expired request.
     * Returns null when nothing was pending (the other path already answered it).
     */
    @Synchronized
    fun resolvePendingRaise(answer: RaiseResult): RaiseResolution? {
        val p = pending ?: return null
        pending = null
        val result = if (answer != RaiseResult.TIMEOUT && clock() >= p.deadline) RaiseResult.TIMEOUT else answer
        if (result == RaiseResult.ALLOWED) approveRaise(p.mode)
        return RaiseResolution(p.mode, result)
    }

    /** Allow the pending raise for [raiseDurationMs]. Returns the mode now in force, or null if nothing was pending. */
    @Synchronized
    fun allowPendingRaise(): ViewMode? {
        val resolved = resolvePendingRaise(RaiseResult.ALLOWED) ?: return null
        return if (resolved.result == RaiseResult.ALLOWED) effectiveMode() else null
    }

    /** Keep private: the pending raise is dropped; the cap does not move. Returns true if one was pending. */
    @Synchronized
    fun denyPendingRaise(): Boolean = resolvePendingRaise(RaiseResult.DENIED) != null

    @get:Synchronized
    var policy: Policy = Policy.DEFAULT
        private set

    /** The cap the phone itself enforces, before any policy. */
    @get:Synchronized
    val cap: ViewMode
        get() = stationCap

    /** The cap the server should see: an approved raise is reported as the raised cap while it lasts,
     *  so the dashboard's effective mode follows the approval. It falls back on expiry (see effectiveMode). */
    @Synchronized
    fun reportedCap(): ViewMode {
        effectiveMode() // lapses an expired raise first
        return if (raiseApproved) approvedMode else stationCap
    }

    private var raisedUntil: Long = 0L
    private var raiseApproved = false
    private var approvedMode: ViewMode = ViewMode.FULL

    /** True once a server `policy` arrived in this monitoring session. From then on the policy is the
     *  only remote control: the legacy `privacy` downlink is ignored (it would otherwise lower the
     *  station's own cap, and every later raise would need an on-device approval). */
    private var policySeen = false

    /** Start of a monitoring session: nothing from the previous connection's policy carries over. */
    @Synchronized
    fun beginSession() {
        policySeen = false
        pending = null
    }

    /** Set by the device UI: the person holding the phone chose this. This is the only way to raise. */
    @Synchronized
    fun setStationCapFromDevice(mode: ViewMode) {
        stationCap = mode
        pending = null
        raiseApproved = false
        raisedUntil = 0L
        approvedMode = ViewMode.FULL
    }

    /** A server `policy` downlink. Lowering is applied at once; it can never raise the cap. */
    @Synchronized
    fun onPolicy(next: Policy) {
        policy = next
        policySeen = true
    }

    /**
     * A server `mode_request`: the owner wants [mode].
     * Returns what happened so the service can show the right thing.
     */
    @Synchronized
    fun onModeRequest(mode: ViewMode, fromOwner: Boolean): RequestOutcome =
        when {
            mode.rank <= effectiveMode().rank -> RequestOutcome.Applied(effectiveMode(), ModeReason.POLICY)
            allowOwnerRaiseWithoutPrompt && fromOwner -> {
                approveRaise(mode)
                RequestOutcome.Applied(effectiveMode(), ModeReason.APPROVED_RAISE)
            }
            else -> {
                pending = PendingRaise(mode, clock() + requestTimeoutMs)
                RequestOutcome.NeedsApproval(mode)
            }
        }

    /** The device UI approved a raise. It lasts [raiseDurationMs], then the old cap returns. */
    @Synchronized
    fun approveRaise(mode: ViewMode) {
        if (mode.rank <= stationCap.rank) return  // no raise needed; nothing to remember
        raiseApproved = true
        approvedMode = mode
        raisedUntil = clock() + raiseDurationMs
    }

    /** Whether the legacy `privacy` downlink must be ignored: a `policy` has been seen this session. */
    @Synchronized
    fun ignoresLegacyPrivacy(): Boolean = policySeen

    /**
     * The legacy `privacy` downlink, for servers that never send `policy` (old APKs keep working
     * there). Once a policy was seen it is ignored completely. Before that, `true` lowers the cap to
     * blackout. It can NEVER raise exposure: a remote `privacy:false` is ignored on purpose.
     */
    @Synchronized
    fun onLegacyPrivacy(enabled: Boolean): ViewMode {
        if (enabled && !ignoresLegacyPrivacy()) {
            setStationCapFromDevice(ViewMode.BLACKOUT)
        }
        return effectiveMode()
    }

    /** The mode that actually applies right now, accounting for an expired raise. */
    @Synchronized
    fun effectiveMode(): ViewMode {
        if (raiseApproved && clock() >= raisedUntil) {
            raiseApproved = false
            raisedUntil = 0L
        }
        // An approved raise temporarily lifts the *cap* to what the person allowed; the server's
        // policy still applies on top, so a policy of `privacy` cannot be raised past it.
        val effectiveCap = if (raiseApproved) approvedMode else stationCap
        return effectiveCap.minOf(policy.mode)
    }

    /** Cap, server policy, effective mode and reason, read under one lock: a consistent view for callers and tests. */
    @Synchronized
    fun snapshot(): ModeSnapshot {
        val effective = effectiveMode()
        return ModeSnapshot(stationCap, policy.mode, effective, effectiveReason())
    }

    fun effectiveReason(): ModeReason = when {
        raiseApproved -> ModeReason.APPROVED_RAISE
        stationCap.rank <= policy.mode.rank -> ModeReason.STATION
        else -> ModeReason.POLICY
    }

    /** Whether a camera preview should be produced and uploaded at all. */
    @Synchronized
    fun shouldEncodePreview(): Boolean {
        val mode = effectiveMode()
        if (mode.rank < ViewMode.LIMITED.rank) return false   // blackout/privacy: no image at all
        if (policy.videoViewers <= 0) return false           // nobody is watching: don't spend battery
        return true
    }

    /** Whether raw audio may be streamed right now (T3.9). The rule itself lives in [AudioStreamGate]. */
    @Synchronized
    fun shouldStreamAudio(): Boolean =
        AudioStreamGate.shouldStream(effectiveMode(), policy.audio, policy.audioViewers)

    sealed class RequestOutcome {
        /** Applied immediately: it lowered exposure, or a raise was already approved. */
        data class Applied(val mode: ViewMode, val reason: ModeReason) : RequestOutcome()

        /** The phone must ask the person holding it before anything is raised. */
        data class NeedsApproval(val mode: ViewMode) : RequestOutcome()
    }
}

/** A consistent read of the controller (see [ModeController.snapshot]). */
data class ModeSnapshot(val cap: ViewMode, val policy: ViewMode, val effective: ViewMode, val reason: ModeReason)

/** The on-device prompt for a raise, shared by the dialog and the notification so both say the same thing. */
fun approvalQuestion(mode: ViewMode): String {
    val label = when (mode) {
        ViewMode.BLACKOUT -> "Blackout"
        ViewMode.PRIVACY -> "Privacy"
        ViewMode.LIMITED -> "Limited"
        ViewMode.FULL -> "Full"
    }
    return "A remote viewer asks to raise sharing to $label. Allow for 15 minutes?"
}
