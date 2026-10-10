package com.btb.ondevice.capture

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
    fun pendingRaise(): PendingRaise? {
        val p = pending ?: return null
        if (clock() >= p.deadline) {
            pending = null
            return null
        }
        return p
    }

    /** Allow the pending raise for [raiseDurationMs]. Returns the mode now in force, or null if nothing was pending. */
    fun allowPendingRaise(): ViewMode? {
        val p = pendingRaise() ?: return null
        pending = null
        approveRaise(p.mode)
        return effectiveMode()
    }

    /** Keep private: the pending raise is dropped; the cap does not move. Returns true if one was pending. */
    fun denyPendingRaise(): Boolean {
        val had = pendingRaise() != null
        pending = null
        return had
    }

    var policy: Policy = Policy.DEFAULT
        private set

    /** The cap the phone itself enforces, before any policy. */
    val cap: ViewMode
        get() = stationCap

    private var raisedUntil: Long = 0L
    private var raiseApproved = false
    private var approvedMode: ViewMode = ViewMode.FULL

    /** True once a server `policy` arrived in this monitoring session. From then on the policy is the
     *  only remote control: the legacy `privacy` downlink is ignored (it would otherwise lower the
     *  station's own cap, and every later raise would need an on-device approval). */
    private var policySeen = false

    /** Start of a monitoring session: nothing from the previous connection's policy carries over. */
    fun beginSession() {
        policySeen = false
        pending = null
    }

    /** Set by the device UI: the person holding the phone chose this. This is the only way to raise. */
    fun setStationCapFromDevice(mode: ViewMode) {
        stationCap = mode
        pending = null
        raiseApproved = false
        raisedUntil = 0L
        approvedMode = ViewMode.FULL
    }

    /** A server `policy` downlink. Lowering is applied at once; it can never raise the cap. */
    fun onPolicy(next: Policy) {
        policy = next
        policySeen = true
    }

    /**
     * A server `mode_request`: the owner wants [mode].
     * Returns what happened so the service can show the right thing.
     */
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
    fun approveRaise(mode: ViewMode) {
        if (mode.rank <= stationCap.rank) return  // no raise needed; nothing to remember
        raiseApproved = true
        approvedMode = mode
        raisedUntil = clock() + raiseDurationMs
    }

    /** Whether the legacy `privacy` downlink must be ignored: a `policy` has been seen this session. */
    fun ignoresLegacyPrivacy(): Boolean = policySeen

    /**
     * The legacy `privacy` downlink, for servers that never send `policy` (old APKs keep working
     * there). Once a policy was seen it is ignored completely. Before that, `true` lowers the cap to
     * blackout. It can NEVER raise exposure: a remote `privacy:false` is ignored on purpose.
     */
    fun onLegacyPrivacy(enabled: Boolean): ViewMode {
        if (enabled && !ignoresLegacyPrivacy()) {
            setStationCapFromDevice(ViewMode.BLACKOUT)
        }
        return effectiveMode()
    }

    /** The mode that actually applies right now, accounting for an expired raise. */
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

    fun effectiveReason(): ModeReason = when {
        raiseApproved -> ModeReason.APPROVED_RAISE
        stationCap.rank <= policy.mode.rank -> ModeReason.STATION
        else -> ModeReason.POLICY
    }

    /** Whether a camera preview should be produced and uploaded at all. */
    fun shouldEncodePreview(): Boolean {
        val mode = effectiveMode()
        if (mode.rank < ViewMode.LIMITED.rank) return false   // blackout/privacy: no image at all
        if (policy.videoViewers <= 0) return false           // nobody is watching: don't spend battery
        return true
    }

    /** Whether raw audio may be streamed (T3.9). */
    fun shouldStreamAudio(): Boolean =
        effectiveMode() == ViewMode.FULL && policy.audio && policy.audioViewers > 0

    sealed class RequestOutcome {
        /** Applied immediately: it lowered exposure, or a raise was already approved. */
        data class Applied(val mode: ViewMode, val reason: ModeReason) : RequestOutcome()

        /** The phone must ask the person holding it before anything is raised. */
        data class NeedsApproval(val mode: ViewMode) : RequestOutcome()
    }
}

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
