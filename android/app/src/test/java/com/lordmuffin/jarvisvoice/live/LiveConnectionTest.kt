package com.lordmuffin.jarvisvoice.live

import com.lordmuffin.jarvisvoice.live.protocol.AudioFrame
import com.lordmuffin.jarvisvoice.live.protocol.Channel
import com.lordmuffin.jarvisvoice.live.protocol.ClientMessage
import com.lordmuffin.jarvisvoice.live.protocol.DraftSegment
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import okio.ByteString
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.util.Collections

class LiveConnectionTest {
    @get:Rule val tmp = TemporaryFolder()

    private lateinit var server: MockWebServer
    private lateinit var fake: FakeLiveServer
    private lateinit var scope: CoroutineScope

    private val sessionId = "3f2b8c1e-5d4a-4b7e-9a10-2c6d8e9f0a1b"
    private val fastConfig = LiveConnection.Config(
        reconnect = ReconnectPolicy(initialDelayMs = 10, maxDelayMs = 50),
        handshakeTimeoutMs = 2_000,
        endGracePeriodMs = 2_000,
    )

    @Before fun setUp() {
        fake = FakeLiveServer()
        server = MockWebServer().apply { dispatcher = fake; start() }
        scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    }

    @After fun tearDown() {
        scope.cancel()
        server.shutdown()
    }

    private fun api() = LiveApiClient(server.url("/").toString(), "token-1")

    private fun outboxWith(frames: Int): Outbox {
        val outbox = Outbox.open(tmp.root, sessionId)
        repeat(frames) { outbox.append(Channel.MIC, ByteArray(AudioFrame.FRAME_BYTES) { _ -> it.toByte() }, it * 100L) }
        return outbox
    }

    @Test fun streamsEverythingThenEnds() = runBlocking {
        val outbox = outboxWith(10)
        val conn = LiveConnection(api(), sessionId, "android:test", outbox, scope, fastConfig)
        conn.sendDraft(ClientMessage.Draft(DraftSegment(Channel.MIC, 0, 900, "hi", final = true)))
        conn.start()
        conn.enqueueAudio(Channel.MIC, ByteArray(AudioFrame.FRAME_BYTES), 1000)

        val terminal = withTimeout(10_000) { conn.end() }

        assertEquals(ConnState.Ended, terminal)
        assertEquals((0L..10L).toList(), fake.received)
        assertTrue(fake.gaps.isEmpty())
        assertTrue(fake.texts.any { it.contains("\"draft_segment\"") })
        assertEquals(1, fake.endCount)
        assertEquals("Live note", conn.finalNote?.title)
        assertEquals(0, outbox.totalUnackedMs())
        assertEquals("Bearer token-1", fake.authHeaders.first())
        assertTrue(fake.hellos.first().contains("\"device\":\"android:test\""))
    }

    @Test fun resumesAfterDropWithoutGaps() = runBlocking {
        fake.dropAfterFrames = 5
        fake.ackEvery = 3   // the server only acks some of what it stored before dropping
        val outbox = outboxWith(20)
        val conn = LiveConnection(api(), sessionId, "android:test", outbox, scope, fastConfig)
        conn.start()

        val terminal = withTimeout(10_000) { conn.end() }

        assertEquals(ConnState.Ended, terminal)
        assertTrue("expected a reconnect, got ${fake.connections}", fake.connections >= 2)
        assertTrue(fake.gaps.isEmpty())
        assertEquals((0L..19L).toList(), fake.received.distinct())
        assertEquals(2, fake.tickets)  // a fresh ticket per connection
    }

    @Test fun ticketRejectionIsFatal() = runBlocking {
        fake.ticketStatus = 401
        val conn = LiveConnection(api(), sessionId, "android:test", outboxWith(3), scope, fastConfig)
        val terminal = withTimeout(10_000) { conn.end() }
        assertTrue(terminal is ConnState.Failed)
        assertEquals(1, fake.tickets)
    }

    @Test fun sessionEndedElsewhereIsFatal() = runBlocking {
        fake.closeOnHelloCode = 4410
        val conn = LiveConnection(api(), sessionId, "android:test", outboxWith(3), scope, fastConfig)
        val terminal = withTimeout(10_000) { conn.end() }
        assertTrue(terminal.toString(), terminal is ConnState.Failed)
    }

    @Test fun backpressureCapsUnackedAudio() = runBlocking {
        fake.ackEvery = Int.MAX_VALUE  // never ack
        val outbox = outboxWith(100)
        val conn = LiveConnection(
            api(), sessionId, "android:test", outbox, scope, fastConfig.copy(maxUnackedAudioMs = 1_000),
        )
        conn.start()
        delay(1_000)
        // Sends while in-flight audio ≤ 1000 ms: 11 frames of 100 ms, then waits for acks.
        assertEquals(11, fake.received.size)
        conn.stop()
    }

    /** A minimal Jarvis Live: ticket endpoint plus the producer side of the stream protocol. */
    private inner class FakeLiveServer : Dispatcher() {
        val received: MutableList<Long> = Collections.synchronizedList(ArrayList())
        val gaps: MutableList<String> = Collections.synchronizedList(ArrayList())
        val texts: MutableList<String> = Collections.synchronizedList(ArrayList())
        val hellos: MutableList<String> = Collections.synchronizedList(ArrayList())
        val authHeaders: MutableList<String> = Collections.synchronizedList(ArrayList())
        @Volatile var ticketStatus = 200
        @Volatile var tickets = 0
        @Volatile var connections = 0
        @Volatile var endCount = 0
        @Volatile var dropAfterFrames = Int.MAX_VALUE
        @Volatile var ackEvery = 1
        @Volatile var closeOnHelloCode: Int? = null

        /** Highest contiguous seq stored (the server's durable cursor). */
        @Volatile private var stored: Long? = null
        @Volatile private var acked: Long? = null

        override fun dispatch(request: RecordedRequest): MockResponse {
            val path = request.requestUrl!!.encodedPath
            authHeaders += request.getHeader("Authorization").orEmpty()
            return when {
                path == "/v1/sessions/$sessionId/ticket" -> {
                    tickets++
                    if (ticketStatus != 200) MockResponse().setResponseCode(ticketStatus).setBody("""{"detail":"nope"}""")
                    else MockResponse().setBody("""{"ticket":"t$tickets","expires_in":60}""")
                }
                path == "/v1/sessions/$sessionId/stream" -> {
                    connections++
                    MockResponse().withWebSocketUpgrade(Producer(connections))
                }
                else -> MockResponse().setResponseCode(404)
            }
        }

        private inner class Producer(private val connection: Int) : WebSocketListener() {
            private var framesThisConnection = 0
            private var sinceAck = 0

            override fun onMessage(webSocket: WebSocket, text: String) {
                val type = JSONObject(text).getString("type")
                texts += text
                when (type) {
                    "hello" -> {
                        hellos += text
                        closeOnHelloCode?.let { webSocket.close(it, "session ended"); return }
                        // Like the server, hello_ack reports what is durably stored.
                        acked = stored
                        val mic = stored?.toString() ?: "null"
                        webSocket.send("""{"type":"hello_ack","session":"$sessionId","acked":{"mic":$mic,"system":null}}""")
                    }
                    "end" -> {
                        endCount++
                        flushAck(webSocket)
                        webSocket.send("""{"type":"final_note","path":"Meetings/x.md","title":"Live note"}""")
                        webSocket.close(1000, null)
                    }
                }
            }

            override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
                val frame = AudioFrame.decode(bytes.toByteArray())
                val expected = (stored ?: -1) + 1
                when {
                    frame.seq < expected -> Unit  // duplicate: already stored
                    frame.seq > expected -> gaps += "conn $connection: expected $expected got ${frame.seq}"
                    else -> stored = frame.seq
                }
                received += frame.seq
                framesThisConnection++
                if (++sinceAck >= ackEvery) flushAck(webSocket)
                if (connection == 1 && framesThisConnection >= dropAfterFrames) webSocket.cancel()
            }

            private fun flushAck(webSocket: WebSocket) {
                sinceAck = 0
                val seq = stored ?: return
                if (acked == seq) return
                acked = seq
                webSocket.send("""{"type":"ack","channel":"mic","seq":$seq}""")
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) = Unit
        }
    }
}
