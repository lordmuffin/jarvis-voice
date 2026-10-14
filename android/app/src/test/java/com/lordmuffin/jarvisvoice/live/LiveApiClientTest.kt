package com.lordmuffin.jarvisvoice.live

import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okio.Buffer
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

/** The history and sign-in REST calls against a fake server. */
class LiveApiClientTest {

    @get:Rule val tmp = TemporaryFolder()

    private lateinit var server: MockWebServer
    private lateinit var client: LiveApiClient

    @Before fun setUp() {
        server = MockWebServer().apply { start() }
        client = LiveApiClient(server.url("/").toString(), "tok-123")
    }

    @After fun tearDown() {
        server.shutdown()
    }

    @Test fun meSendsTheBearerToken() = runBlocking<Unit> {
        server.enqueue(MockResponse().setBody("""{"id": "u1", "email": null, "display_name": "Andrew", "is_admin": false, "device_id": "d1"}"""))
        val me = client.me()
        assertEquals("Andrew", me.displayName)
        val req = server.takeRequest()
        assertEquals("GET", req.method)
        assertEquals("/v1/me", req.path)
        assertEquals("Bearer tok-123", req.getHeader("Authorization"))
    }

    @Test fun listSessionsPassesPaging() = runBlocking<Unit> {
        server.enqueue(MockResponse().setBody("""{"items": [{"id": "s1", "status": "done"}], "total": 41, "limit": 20, "offset": 40}"""))
        val page = client.listSessions(limit = 20, offset = 40)
        assertEquals("s1", page.items.single().id)
        assertFalse(page.hasMore)
        assertEquals("/v1/sessions?limit=20&offset=40", server.takeRequest().path)
    }

    @Test fun getSessionEncodesTheId() = runBlocking<Unit> {
        server.enqueue(MockResponse().setBody("""{"id": "s1", "segments": [], "audio": {"mic": true}}"""))
        val d = client.getSession("s1")
        assertEquals(listOf("mic"), d.audioChannels)
        assertEquals("/v1/sessions/s1", server.takeRequest().path)
    }

    @Test fun unauthorizedIsALiveHttpException() = runBlocking<Unit> {
        server.enqueue(MockResponse().setResponseCode(401).setBody("""{"detail": "bad token"}"""))
        try {
            client.listSessions()
            fail("expected 401")
        } catch (e: LiveHttpException) {
            assertEquals(401, e.status)
            assertTrue(e.isFatal)
        }
    }

    @Test fun downloadAudioWritesTheFile() = runBlocking<Unit> {
        val wav = ByteArray(1000) { it.toByte() }
        server.enqueue(MockResponse().setBody(Buffer().write(wav)).setHeader("Content-Type", "audio/wav"))
        val dest = tmp.root.resolve("audio/s1_mic.wav")
        var last = 0L
        client.downloadAudio("s1", "mic", dest) { done, _ -> last = done }
        assertArrayEquals(wav, dest.readBytes())
        assertEquals(1000L, last)
        assertFalse(java.io.File(dest.path + ".part").exists())
        val req = server.takeRequest()
        assertEquals("/v1/sessions/s1/audio/mic.wav", req.path)
        assertEquals("Bearer tok-123", req.getHeader("Authorization"))
    }

    @Test fun downloadAudio404LeavesNoFile() = runBlocking<Unit> {
        server.enqueue(MockResponse().setResponseCode(404).setBody("""{"detail": "no audio"}"""))
        val dest = tmp.root.resolve("s1_system.wav")
        try {
            client.downloadAudio("s1", "system", dest)
            fail("expected 404")
        } catch (e: LiveHttpException) {
            assertEquals(404, e.status)
        }
        assertFalse(dest.exists())
    }

    @Test fun redeemDeviceCodePostsWithoutBearer() = runBlocking<Unit> {
        server.enqueue(MockResponse().setBody("""{"device_id": "d9", "token": "new-token"}"""))
        val anon = LiveApiClient(server.url("/").toString(), "")
        val token = anon.redeemDeviceCode("code-1", "v".repeat(43), "Google Pixel 8")
        assertEquals(DeviceToken("d9", "new-token"), token)
        val req = server.takeRequest()
        assertEquals("POST", req.method)
        assertEquals("/v1/auth/device-code", req.path)
        assertNull(req.getHeader("Authorization"))
        val body = JSONObject(req.body.readUtf8())
        assertEquals("code-1", body.getString("code"))
        assertEquals("v".repeat(43), body.getString("verifier"))
        assertEquals("Google Pixel 8", body.getString("device_name"))
    }

    @Test fun redeemRejectedCodeIs400() = runBlocking<Unit> {
        server.enqueue(MockResponse().setResponseCode(400).setBody("""{"detail": "invalid or expired code"}"""))
        try {
            client.redeemDeviceCode("used", "v".repeat(43), "Pixel")
            fail("expected 400")
        } catch (e: LiveHttpException) {
            assertEquals(400, e.status)
        }
    }

    @Test fun renameSendsPatch() = runBlocking<Unit> {
        server.enqueue(MockResponse().setBody("""{"id": "s1"}"""))
        client.renameSession("s1", "  New name ")
        val req = server.takeRequest()
        assertEquals("PATCH", req.method)
        assertEquals("New name", JSONObject(req.body.readUtf8()).getString("title"))
    }

    @Test fun reachableWhenHealthzSaysOk() = runBlocking<Unit> {
        server.enqueue(MockResponse().setBody("""{"status": "ok"}"""))
        assertTrue(client.isReachable())
        val req = server.takeRequest()
        assertEquals("/healthz", req.path)
    }

    @Test fun unreachableOnAnythingElse() = runBlocking<Unit> {
        // A captive portal's login page, a proxy error, then nothing listening at all.
        server.enqueue(MockResponse().setBody("<html>Sign in to Hotel Wi-Fi</html>"))
        assertFalse(client.isReachable())
        server.enqueue(MockResponse().setResponseCode(502).setBody("""{"status": "ok"}"""))
        assertFalse(client.isReachable())
        val gone = MockWebServer().apply { start() }
        val url = gone.url("/").toString()
        gone.shutdown()
        assertFalse(LiveApiClient(url, "tok-123").isReachable())
    }

    @Test fun channelReadsTheEnvironmentFromHealthz() = runBlocking<Unit> {
        server.enqueue(MockResponse().setBody("""{"status": "ok", "environment": "lab", "version": "0.12.0", "commit": "abc1234"}"""))
        assertEquals(ServerChannel.Lab, client.channel())
        assertEquals("/healthz", server.takeRequest().path)
    }

    @Test fun channelIsUnknownWhenTheServerDoesNotSay() = runBlocking<Unit> {
        // An older server, a proxy error, then nothing listening at all.
        server.enqueue(MockResponse().setBody("""{"status": "ok"}"""))
        assertNull(client.channel())
        server.enqueue(MockResponse().setResponseCode(502).setBody("""{"status": "ok", "environment": "live"}"""))
        assertNull(client.channel())
        val gone = MockWebServer().apply { start() }
        val url = gone.url("/").toString()
        gone.shutdown()
        assertNull(LiveApiClient(url, "tok-123").channel())
    }
}
