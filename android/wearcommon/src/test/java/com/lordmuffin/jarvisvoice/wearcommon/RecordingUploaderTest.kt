package com.lordmuffin.jarvisvoice.wearcommon

import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

class RecordingUploaderTest {
    @get:Rule val tmp = TemporaryFolder()
    private lateinit var server: MockWebServer

    @Before fun up() { server = MockWebServer().also { it.start() } }
    @After fun down() { runCatching { server.shutdown() } }

    private fun upload(): UploadResult {
        val f = java.io.File(tmp.root, "w-1.aac").apply { writeBytes(ByteArray(2048) { it.toByte() }) }
        val up = RecordingUploader(server.url("/").toString(), "tok-123")
        return runBlocking { up.upload(f, clientId = "w-1", title = "Watch memo", source = "watch") }
    }

    @Test fun `posts the raw file with the bearer token, source and client id`() {
        server.enqueue(MockResponse().setResponseCode(202).setBody("""{"id":"3f2c0c5e-0000-4000-8000-000000000001"}"""))
        assertEquals(UploadResult.Acked("3f2c0c5e-0000-4000-8000-000000000001"), upload())

        val req = server.takeRequest()
        assertEquals("POST", req.method)
        assertEquals("/v1/sessions/upload", req.requestUrl!!.encodedPath)
        assertEquals("Bearer tok-123", req.getHeader("Authorization"))
        assertEquals("watch", req.requestUrl!!.queryParameter("source"))
        assertEquals("w-1", req.requestUrl!!.queryParameter("client_id"))
        assertEquals("Watch memo", req.requestUrl!!.queryParameter("title"))
        assertEquals(2048, req.bodySize.toInt())
    }

    @Test fun `server trouble and 429 are retried`() {
        for (code in listOf(500, 502, 503, 429, 404)) {
            server.enqueue(MockResponse().setResponseCode(code))
            assertTrue("$code", upload() is UploadResult.Retry)
        }
    }

    @Test fun `a revoked token is an auth rejection`() {
        server.enqueue(MockResponse().setResponseCode(401))
        assertTrue(upload() is UploadResult.AuthRejected)
        server.enqueue(MockResponse().setResponseCode(403))
        assertTrue(upload() is UploadResult.AuthRejected)
    }

    @Test fun `a file the server refuses is rejected, not retried`() {
        for (code in listOf(413, 415, 422)) {
            server.enqueue(MockResponse().setResponseCode(code).setBody("no audio"))
            assertTrue("$code", upload() is UploadResult.Rejected)
        }
    }

    @Test fun `a 200 that is not the jarvis server is not an ack`() {
        server.enqueue(MockResponse().setResponseCode(200).setBody("<html>sign in to wifi</html>"))
        assertTrue(upload() is UploadResult.Retry)
    }

    @Test fun `an unreachable server is a retry`() {
        server.shutdown()
        assertTrue(upload() is UploadResult.Retry)
    }
}
