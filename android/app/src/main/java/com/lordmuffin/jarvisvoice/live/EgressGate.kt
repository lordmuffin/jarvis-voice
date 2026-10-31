package com.lordmuffin.jarvisvoice.live

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import okhttp3.Interceptor
import java.io.IOException
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.atomic.AtomicInteger

/** A request was refused because a local-only session is recording. */
class EgressBlockedException : IOException("Blocked: a local-only session is recording, so nothing leaves this phone.")

/**
 * Decides whether Jarvis Live may use the network. While a local-only session records every
 * request through [LiveApiClient.defaultClient] (REST, health checks and the WebSocket upgrade) is
 * refused, so a new Live feature cannot leak by forgetting to ask, and a [LiveConnection] already
 * open is cut through [whenShut]. `scripts/lint/check_egress.py` fails CI on the usual other ways
 * of opening a connection.
 *
 * Not covered: the watch uploader (`RecordingUploader`) and the features outside Live sessions
 * (chat, TTS, model downloads). See `docs/plans/local-only-audit.md`.
 */
class EgressGate {
    private val localSessions = AtomicInteger(0)
    private val onShut = CopyOnWriteArrayList<() -> Unit>()
    private val _blocking = MutableStateFlow(false)

    val isBlocking: Boolean get() = localSessions.get() > 0

    /** [isBlocking] as a flow, so the network can be looked at again once it opens. */
    val blocking: StateFlow<Boolean> = _blocking.asStateFlow()

    fun beginLocalSession() {
        val first = localSessions.incrementAndGet() == 1
        _blocking.value = true
        if (first) onShut.forEach { it() }
    }

    fun endLocalSession() {
        _blocking.value = localSessions.updateAndGet { maxOf(0, it - 1) } > 0
    }

    /** Runs [action] each time the gate shuts, on the caller's thread. Returns what removes it. */
    fun whenShut(action: () -> Unit): () -> Unit {
        onShut.add(action)
        return { onShut.remove(action) }
    }

    @Throws(EgressBlockedException::class)
    fun check() {
        if (isBlocking) throw EgressBlockedException()
    }

    /** For the one OkHttp client Live uses. */
    val interceptor: Interceptor = Interceptor { chain: Interceptor.Chain ->
        check()
        chain.proceed(chain.request())
    }

    companion object {
        val shared = EgressGate()
    }
}
