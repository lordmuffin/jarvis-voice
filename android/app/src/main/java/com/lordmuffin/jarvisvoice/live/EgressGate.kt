package com.lordmuffin.jarvisvoice.live

import okhttp3.Interceptor
import java.io.IOException
import java.util.concurrent.atomic.AtomicInteger

/** A request was refused because a local-only session is recording. */
class EgressBlockedException : IOException("Blocked: a local-only session is recording, so nothing leaves this phone.")

/**
 * Decides whether Jarvis Live may use the network. While a local-only session records every
 * request through [LiveApiClient.defaultClient] (REST, health checks and the WebSocket upgrade) is
 * refused, so a new Live feature cannot leak by forgetting to ask. `scripts/lint/check_egress.py`
 * fails CI on any other way of opening a Live connection.
 *
 * Not covered: the watch uploader (`RecordingUploader`) and the features outside Live sessions
 * (chat, TTS, model downloads). See `docs/plans/local-only-audit.md`.
 */
class EgressGate {
    private val localSessions = AtomicInteger(0)

    val isBlocking: Boolean get() = localSessions.get() > 0

    fun beginLocalSession() {
        localSessions.incrementAndGet()
    }

    fun endLocalSession() {
        localSessions.updateAndGet { maxOf(0, it - 1) }
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
