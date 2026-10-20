package com.lordmuffin.jarvisvoice.live

import com.lordmuffin.jarvisvoice.live.protocol.Channel
import com.lordmuffin.jarvisvoice.live.protocol.ClientMessage
import com.lordmuffin.jarvisvoice.live.protocol.ServerEvent
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.async
import kotlinx.coroutines.cancelChildren
import kotlinx.coroutines.channels.Channel as KChannel
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.selects.select
import kotlinx.coroutines.withTimeoutOrNull
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import okio.ByteString.Companion.toByteString
import java.io.IOException
import kotlin.coroutines.coroutineContext

sealed class ConnState {
    data object Idle : ConnState()
    data object Connecting : ConnState()
    data object Connected : ConnState()
    /** Waiting to retry after a failure; [attempt] is 1 for the first retry. */
    data class Reconnecting(val attempt: Int) : ConnState()
    /** [LiveConnection.end] completed. */
    data object Ended : ConnState()
    /** Unrecoverable (credentials rejected, session ended elsewhere, …). */
    data class Failed(val reason: String) : ConnState()
}

/** The server closed the stream in a way a reconnect cannot fix. */
class FatalStreamException(message: String) : IOException(message)

/**
 * A resumable producer connection for one session — a port of the Mac's
 * `JarvisLiveKit/LiveConnection`.
 *
 * Audio enters through [enqueueAudio]; the [Outbox] is the only record of what was captured, and
 * only the server's cumulative `ack` frees data. On every (re)connect the connection fetches a
 * fresh ticket, sends `hello` with the outbox's acked seqs as `resume`, applies the server's
 * `hello_ack`, then replays everything still unacked before streaming new frames.
 */
class LiveConnection(
    private val api: LiveApiClient,
    private val sessionId: String,
    private val device: String,
    private val outbox: Outbox,
    private val scope: CoroutineScope,
    private val config: Config = Config(),
) {
    data class Config(
        /** The send loop pauses while more than this much sent audio is unacked. */
        val maxUnackedAudioMs: Int = 5_000,
        val reconnect: ReconnectPolicy = ReconnectPolicy(),
        /** How long to wait for `hello_ack` after sending `hello`. */
        val handshakeTimeoutMs: Long = 10_000,
        /** After `end` is sent and acked, how long to wait for `final_note` / close. */
        val endGracePeriodMs: Long = 10_000,
    )

    private val _state = MutableStateFlow<ConnState>(ConnState.Idle)
    val state: StateFlow<ConnState> = _state

    private val _events = MutableSharedFlow<ServerEvent>(extraBufferCapacity = 1024)
    /** Server events other than `ack`/`hello_ack`, which the connection consumes itself. */
    val events: SharedFlow<ServerEvent> = _events

    @Volatile var finalNote: ServerEvent.FinalNote? = null
        private set

    private val lock = Any()
    private val pendingControl = ArrayDeque<ClientMessage>()
    private val wakeSignal = KChannel<Unit>(KChannel.CONFLATED)
    private val done = CompletableDeferred<ConnState>()

    private var runJob: Job? = null
    private var failureCount = 0
    @Volatile private var endRequested = false
    @Volatile private var endSent = false
    @Volatile private var finished = false

    // region Public API

    /** Starts connecting. Idempotent. */
    fun start() = synchronized(lock) {
        if (runJob != null || finished) return
        runJob = scope.launch { run() }
    }

    /** Stores a captured frame in the outbox and wakes the send loop. Called from the capture thread. */
    fun enqueueAudio(channel: Channel, payload: ByteArray, tMs: Long) {
        outbox.append(channel, payload, tMs)
        wake()
    }

    fun sendDraft(draft: ClientMessage.Draft) = enqueueControl(draft)

    fun sendMarker(marker: ClientMessage.Marker) = enqueueControl(marker)

    /**
     * Flushes all audio, sends `end`, waits for the server to ack everything (and, briefly, for
     * `final_note`), then closes. Returns the terminal state.
     */
    suspend fun end(): ConnState {
        if (!finished) {
            endRequested = true
            start()
            wake()
        }
        return done.await()
    }

    /** Drops the connection without ending the session; the outbox stays resumable. */
    fun stop() {
        synchronized(lock) { runJob?.cancel(); runJob = null }
        if (!finished) finish(ConnState.Idle)
    }

    // endregion

    // region Run loop

    private suspend fun run() {
        while (!finished) {
            setState(if (failureCount == 0) ConnState.Connecting else ConnState.Reconnecting(failureCount))
            try {
                runOnce()
                finish(ConnState.Ended)
                return
            } catch (e: CancellationException) {
                throw e
            } catch (e: LiveHttpException) {
                if (e.isFatal) {
                    finish(ConnState.Failed("HTTP ${e.status}: ${e.body}"))
                    return
                }
            } catch (e: FatalStreamException) {
                finish(ConnState.Failed(e.message ?: "stream closed"))
                return
            } catch (_: Exception) {
                // Transient: fall through to backoff and retry.
            }
            if (finished) return
            val delayMs = config.reconnect.delayMs(failureCount)
            failureCount += 1
            setState(ConnState.Reconnecting(failureCount))
            delay(delayMs)
        }
    }

    private suspend fun runOnce() {
        val ticket = api.ticket(sessionId)
        val socket = Socket.open(api.http, api.streamUrl(sessionId, ticket))
        endSent = false  // `end` is re-sent on a new connection if it was not fully acked.
        try {
            converse(socket)
        } finally {
            socket.close()
        }
    }

    private suspend fun converse(socket: Socket) {
        // Handshake: hello (with resume from the outbox) → hello_ack.
        socket.sendText(ClientMessage.Hello(device, outbox.ackedSeqs()).encode())
        val helloAck = withTimeoutOrNull(config.handshakeTimeoutMs) { receiveHelloAck(socket) }
            ?: throw IOException("hello_ack timeout")
        for (channel in Channel.entries) {
            helloAck.acked[channel]?.let { outbox.markAcked(channel, it) }
        }
        failureCount = 0
        setState(ConnState.Connected)

        // Everything past the (now authoritative) ack is replayed by the send loop.
        val nextToSend = HashMap<Channel, Long>()
        for (channel in Channel.entries) {
            nextToSend[channel] = outbox.ackedSeq(channel)?.let { it + 1 } ?: 0
        }

        coroutineScope {
            val receiver = async { receiveLoop(socket) }
            val sender = async {
                sendLoop(socket, nextToSend)
                // `end` is sent and fully acked; give the server a moment to emit final_note / close.
                delay(config.endGracePeriodMs)
            }
            // Whichever finishes first decides: a throw means reconnect, a return means ended.
            select<Unit> {
                receiver.onAwait {}
                sender.onAwait {}
            }
            coroutineContext.cancelChildren()
        }
    }

    // endregion

    // region Receive

    private suspend fun receiveHelloAck(socket: Socket): ServerEvent.HelloAck {
        while (true) {
            val event = parse(socket.receive()) ?: continue
            if (event is ServerEvent.HelloAck) return event
            if (event is ServerEvent.Error) {
                _events.tryEmit(event)
                checkFatalError(event)
            }
            // Anything else before hello_ack is ignored.
        }
    }

    private suspend fun receiveLoop(socket: Socket) {
        while (true) {
            val text = try {
                socket.receive()
            } catch (e: FatalStreamException) {
                throw e
            } catch (e: Exception) {
                // Once `end` is out, the server closing the socket is the normal way to finish,
                // but only if it had acked everything first; otherwise reconnect and replay.
                if (endSent && isFullyAcked()) return
                throw e
            }
            when (val event = parse(text) ?: continue) {
                is ServerEvent.Ack -> {
                    outbox.markAcked(event.channel, event.seq)
                    wake()
                }
                is ServerEvent.HelloAck -> Unit
                is ServerEvent.FinalNote -> {
                    finalNote = event
                    _events.tryEmit(event)
                    if (endSent && isFullyAcked()) return
                }
                is ServerEvent.Error -> {
                    _events.tryEmit(event)
                    checkFatalError(event)
                }
                else -> _events.tryEmit(event)
            }
        }
    }

    private fun parse(text: String): ServerEvent? = runCatching { ServerEvent.parse(text) }.getOrNull()

    private fun checkFatalError(error: ServerEvent.Error) {
        if (error.code in FATAL_ERROR_CODES) throw FatalStreamException("${error.code}: ${error.message}")
    }

    // endregion

    // region Send

    private suspend fun sendLoop(socket: Socket, nextToSend: MutableMap<Channel, Long>) {
        while (true) {
            coroutineContext.ensureActive()
            var progressed = false

            while (true) {
                val message = synchronized(lock) { pendingControl.removeFirstOrNull() } ?: break
                if (!socket.sendText(message.encode())) {
                    synchronized(lock) { pendingControl.addFirst(message) }
                    throw IOException("socket closed")
                }
                progressed = true
            }

            for (channel in Channel.entries) {
                val next = nextToSend.getValue(channel)
                var inFlightMs = if (next == 0L) 0 else outbox.unackedDurationMs(channel, through = next - 1)
                if (inFlightMs > config.maxUnackedAudioMs) continue  // backpressure
                for (frame in outbox.unacked(channel, from = next, limit = 16)) {
                    if (inFlightMs > config.maxUnackedAudioMs) break
                    if (!socket.sendBytes(frame.encode())) throw IOException("socket closed")
                    nextToSend[channel] = frame.seq + 1
                    inFlightMs += frame.durationMs
                    progressed = true
                }
            }

            if (endRequested && !progressed) {
                val allSent = Channel.entries.all {
                    outbox.unacked(it, from = nextToSend.getValue(it), limit = 1).isEmpty()
                }
                val controlEmpty = synchronized(lock) { pendingControl.isEmpty() }
                if (allSent && controlEmpty) {
                    if (!endSent) {
                        if (!socket.sendText(ClientMessage.End.encode())) throw IOException("socket closed")
                        endSent = true
                    }
                    if (isFullyAcked()) return
                }
            }

            if (!progressed) wakeSignal.receive()
        }
    }

    private fun isFullyAcked(): Boolean = outbox.totalUnackedMs() == 0

    private fun enqueueControl(message: ClientMessage) {
        if (finished) return
        synchronized(lock) {
            pendingControl.addLast(message)
            if (pendingControl.size > MAX_QUEUED_CONTROL_MESSAGES) {
                // Offline for a long time: shed the oldest draft before anything else.
                val index = pendingControl.indexOfFirst { it is ClientMessage.Draft }
                if (index >= 0) pendingControl.removeAt(index) else pendingControl.removeFirst()
            }
        }
        wake()
    }

    // endregion

    // region State

    private fun setState(new: ConnState) {
        _state.value = new
    }

    private fun finish(terminal: ConnState) {
        synchronized(lock) {
            if (finished) return
            finished = true
        }
        setState(terminal)
        done.complete(terminal)
        wake()
    }

    private fun wake() {
        wakeSignal.trySend(Unit)
    }

    // endregion

    /** Coroutine-friendly wrapper around an OkHttp [WebSocket]. */
    private class Socket private constructor() : WebSocketListener() {
        private val incoming = KChannel<String>(KChannel.UNLIMITED)
        private lateinit var ws: WebSocket

        suspend fun receive(): String = incoming.receive()

        fun sendText(text: String): Boolean = ws.send(text)

        fun sendBytes(bytes: ByteArray): Boolean = ws.send(bytes.toByteString())

        fun close() {
            if (!ws.close(1000, null)) ws.cancel()
        }

        override fun onMessage(webSocket: WebSocket, text: String) {
            incoming.trySend(text)
        }

        override fun onMessage(webSocket: WebSocket, bytes: ByteString) = Unit  // never sent to producers

        override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
            webSocket.close(code, null)
            incoming.close(closeException(code, reason))
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
            incoming.close(closeException(code, reason))
        }

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
            val status = response?.code
            incoming.close(
                if (status != null && status in 400..499 && status != 408 && status != 429) {
                    LiveHttpException(status, response.message)
                } else {
                    IOException("websocket failure", t)
                }
            )
        }

        private fun closeException(code: Int, reason: String): IOException =
            if (code in FATAL_CLOSE_CODES) FatalStreamException("closed $code: $reason")
            else IOException("closed $code: $reason")

        companion object {
            fun open(http: OkHttpClient, url: String): Socket {
                val socket = Socket()
                socket.ws = http.newWebSocket(Request.Builder().url(url).build(), socket)
                return socket
            }
        }
    }

    companion object {
        private const val MAX_QUEUED_CONTROL_MESSAGES = 256

        /**
         * 4404 unknown session, 4409 superseded by a newer producer, 4410 ended over REST
         * (`server/src/jarvis_live/api/stream.py`, `ingest/hub.py`). 4401 (bad/expired ticket) is
         * retried with a fresh ticket.
         */
        val FATAL_CLOSE_CODES = setOf(4404, 4409, 4410)
        val FATAL_ERROR_CODES = setOf("session_not_live")
    }
}
