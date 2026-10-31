package com.lordmuffin.jarvisvoice.live

import com.lordmuffin.jarvisvoice.live.protocol.SessionMode
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** Which stopped sessions the background worker picks up by itself. */
class SessionMetaTest {

    private fun meta(
        localOnly: Boolean = false,
        serverId: String? = null,
        state: SessionMeta.State = SessionMeta.State.PENDING,
        fellBack: Boolean = false,
    ) = SessionMeta(
        id = "s", mode = SessionMode.SOLO, title = null, startedAt = 0, localOnly = localOnly,
        serverId = serverId, state = state, fellBack = fellBack,
    )

    @Test fun interruptedOnlineSessionsResume() {
        assertTrue(meta(serverId = "srv").autoUploads)
    }

    /** The server was down at start, so the recording was meant for processing: upload it once it is back. */
    @Test fun fallbackSessionsUploadByThemselves() {
        assertTrue(meta(localOnly = true, fellBack = true).autoUploads)
    }

    /** A session the user chose to keep local waits for their tap. */
    @Test fun chosenLocalSessionsWaitForTheUser() {
        assertFalse(meta(localOnly = true).autoUploads)
    }

    @Test fun failedAndFinishedSessionsAreLeftAlone() {
        assertFalse(meta(serverId = "srv", state = SessionMeta.State.FAILED).autoUploads)
        assertFalse(meta(localOnly = true, fellBack = true, state = SessionMeta.State.FAILED).autoUploads)
        assertFalse(meta(serverId = "srv", state = SessionMeta.State.DONE).autoUploads)
    }

    @Test fun fellBackSurvivesTheRoundTripAndDefaultsFalseForOldFiles() {
        assertTrue(SessionMeta.fromJson(meta(localOnly = true, fellBack = true).toJson()).fellBack)
        val old = meta(localOnly = true).toJson().apply { remove("fell_back") }
        assertEquals(false, SessionMeta.fromJson(JSONObject(old.toString())).fellBack)
    }

    @Test fun aSessionKeptLocalUploadsRestrictedAndAServerOutageDoesNot() {
        assertTrue(meta(localOnly = true).uploadsRestricted)
        assertFalse(meta(localOnly = true, fellBack = true).uploadsRestricted)
    }
}
