package com.lordmuffin.jarvisvoice.live

import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test
import com.lordmuffin.jarvisvoice.live.protocol.Channel
import com.lordmuffin.jarvisvoice.live.protocol.SessionMode

/** While a local-only session records, the Live client sends nothing. */
class EgressGateTest {

    private lateinit var server: MockWebServer
    private lateinit var gate: EgressGate
    private lateinit var client: LiveApiClient

    @Before fun setUp() {
        server = MockWebServer().apply { start() }
        gate = EgressGate()
        client = LiveApiClient(server.url("/").toString(), "tok", LiveApiClient.defaultClient(gate))
    }

    @After fun tearDown() {
        server.shutdown()
    }

    @Test fun blocksOnlyWhileALocalSessionRecords() {
        assertFalse(gate.isBlocking)
        gate.beginLocalSession()
        assertTrue(gate.isBlocking)
        gate.endLocalSession()
        gate.endLocalSession()  // an extra end never goes below zero
        assertFalse(gate.isBlocking)
    }

    @Test fun overlappingSessionsKeepItShutUntilTheLastEnds() {
        gate.beginLocalSession()
        gate.beginLocalSession()
        gate.endLocalSession()
        assertTrue(gate.isBlocking)
    }

    @Test fun noRequestReachesTheServerWhileBlocked() = runBlocking<Unit> {
        gate.beginLocalSession()
        try {
            client.createSession(SessionMode.MEETING, listOf(Channel.MIC), "Vendor call")
            fail("expected the gate to refuse")
        } catch (e: EgressBlockedException) {
            // expected
        }
        assertFalse(client.isReachable())  // the health check is refused too, not just the API
        assertEquals(0, server.requestCount)

        gate.endLocalSession()
        server.enqueue(MockResponse().setBody("""{"id": "3f2b8c1e-7a4d-4e6b-9c0a-1d2e3f4a5b6c"}"""))
        client.createSession(SessionMode.MEETING, listOf(Channel.MIC), "Vendor call")
        assertEquals(1, server.requestCount)
    }

    @Test fun aRestrictedSessionSaysSoOnTheWire() = runBlocking<Unit> {
        server.enqueue(MockResponse().setBody("""{"id": "3f2b8c1e-7a4d-4e6b-9c0a-1d2e3f4a5b6c"}"""))
        client.createSession(SessionMode.SOLO, listOf(Channel.MIC), null, restricted = true)
        assertTrue(server.takeRequest().body.readUtf8().contains(""""restricted":true"""))
    }

    @Test fun shuttingRunsEachActionOnceAndNotForAnOverlappingSession() {
        var cuts = 0
        gate.whenShut { cuts += 1 }
        gate.beginLocalSession()
        gate.beginLocalSession()
        assertEquals(1, cuts)
    }

    @Test fun aRemovedActionDoesNotRun() {
        var cuts = 0
        val remove = gate.whenShut { cuts += 1 }
        remove()
        gate.beginLocalSession()
        assertEquals(0, cuts)
    }

    @Test fun blockingFollowsTheSessions() {
        assertFalse(gate.blocking.value)
        gate.beginLocalSession()
        gate.beginLocalSession()
        gate.endLocalSession()
        assertTrue(gate.blocking.value)
        gate.endLocalSession()
        assertFalse(gate.blocking.value)
    }
}
