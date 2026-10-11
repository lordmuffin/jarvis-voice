package com.lordmuffin.jarvisvoice.live

/** Where the phone is, as far as reaching Jarvis Live goes. */
enum class NetworkPlace {
    /** The server answered its health check: on the home network. */
    HOME,

    /** A NetBird tunnel is up, so the server is reachable from anywhere. */
    NETBIRD,

    /** Neither: sessions should record on the phone. */
    AWAY;

    companion object {
        fun fromName(name: String?): NetworkPlace? = entries.firstOrNull { it.name == name }
    }
}

/** One network interface as the platform reports it. */
data class NetInterface(val name: String?, val isTunnel: Boolean, val addresses: List<String>)

/** Recognizes a NetBird tunnel from the interfaces alone, without asking the server. */
object NetBird {
    /** NetBird's default overlay range, 100.64.0.0/10. */
    fun isOverlayAddress(address: String): Boolean {
        val parts = address.split('.')
        if (parts.size != 4) return false
        val octets = parts.map { p -> p.toIntOrNull()?.takeIf { it in 0..255 } ?: return false }
        return octets[0] == 100 && octets[1] in 64..127
    }

    /**
     * A tunnel (VPN) with an address in the overlay range. Mobile carriers hand out CGNAT
     * addresses from the same range, hence the tunnel requirement.
     */
    fun detect(interfaces: List<NetInterface>): Boolean =
        interfaces.any { it.isTunnel && it.addresses.any(::isOverlayAddress) }
}

/**
 * Whether a new session records locally. Follows [place] (Local only when [NetworkPlace.AWAY])
 * unless the user flipped the switch. A flip holds until the automatic choice changes: turn
 * Local only off on the train because NetBird is up but went unnoticed, and it comes back on by
 * itself the next time you leave home.
 */
data class AutoLocalOnly(
    /** Settings › "Local only away from home". Off: the switch is purely manual. */
    val enabled: Boolean = true,
    /** The last network check; null until the first one finishes. */
    val place: NetworkPlace? = null,
    /** The user's choice, if they made one. */
    val manual: Boolean? = null,
    /** The automatic value [manual] overrode; null if it was not known yet. */
    val manualAgainst: Boolean? = null,
) {
    /** What the network says, or null before the first check. */
    val automatic: Boolean? get() = place?.let { it == NetworkPlace.AWAY }

    val localOnly: Boolean get() = manual ?: (enabled && automatic == true)

    fun withPlace(place: NetworkPlace): AutoLocalOnly {
        val next = copy(place = place)
        if (manual == null || !enabled) return next
        return when (manualAgainst) {
            null -> next.copy(manualAgainst = next.automatic).settled()
            next.automatic -> next
            else -> next.copy(manual = null, manualAgainst = null)
        }
    }

    fun withManual(localOnly: Boolean): AutoLocalOnly =
        if (!enabled) copy(manual = localOnly, manualAgainst = null)
        else copy(manual = localOnly, manualAgainst = automatic).settled()

    /** Turning automation off keeps what the switch shows; turning it on hands it back. */
    fun withEnabled(enabled: Boolean): AutoLocalOnly = when {
        enabled == this.enabled -> this
        enabled -> copy(enabled = true, manual = null, manualAgainst = null)
        else -> copy(enabled = false, manual = localOnly, manualAgainst = null)
    }

    /** A choice equal to the automatic one is no override. */
    private fun settled(): AutoLocalOnly =
        if (manualAgainst != null && manual == manualAgainst) copy(manual = null, manualAgainst = null) else this
}
