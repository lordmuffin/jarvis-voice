package com.lordmuffin.jarvisvoice.live

/** The connection words shared with web and macOS (`Comp-Status` in the Instrument design). */
enum class ConnectionKind { IDLE, CONNECTING, CONNECTED, RECONNECTING, LOCAL_ONLY, DEGRADED, OFFLINE, FAILED }

/**
 * What the connection chip says. [detail] is the reconnect attempt or the failure reason;
 * [copilotOffline] adds the "Copilot offline" chip beside it (the server's `llm_ok` is false).
 */
data class ConnectionChip(val kind: ConnectionKind, val detail: String? = null, val copilotOffline: Boolean = false) {
    companion object {
        /** Connected, but the server is this many ms behind the audio. */
        const val DEGRADED_LAG_MS = 2_000L

        fun of(conn: ConnState, localOnly: Boolean, lagMs: Long, llmOk: Boolean): ConnectionChip {
            // Local only is a choice, not a fault, and nothing leaves the phone, so it ignores the socket.
            if (localOnly) return ConnectionChip(ConnectionKind.LOCAL_ONLY)
            return when (conn) {
                ConnState.Idle, ConnState.Ended -> ConnectionChip(ConnectionKind.IDLE)
                ConnState.Connecting -> ConnectionChip(ConnectionKind.CONNECTING)
                ConnState.Connected -> ConnectionChip(
                    if (lagMs >= DEGRADED_LAG_MS) ConnectionKind.DEGRADED else ConnectionKind.CONNECTED,
                    copilotOffline = !llmOk,
                )
                is ConnState.Reconnecting -> ConnectionChip(ConnectionKind.RECONNECTING, detail = conn.attempt.toString())
                is ConnState.Failed -> ConnectionChip(ConnectionKind.FAILED, detail = conn.reason)
            }
        }

        /** A `live` session on the server that is not receiving audio (web's "Offline" chip). */
        fun isOffline(status: String, streaming: Boolean): Boolean = status == RemoteSession.STATUS_LIVE && !streaming
    }
}
