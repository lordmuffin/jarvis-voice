package com.lordmuffin.jarvisvoice.live

import com.lordmuffin.jarvisvoice.R
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class ServerChannelTest {

    @Test fun fromTheReportedEnvironment() {
        assertEquals(ServerChannel.Live, ServerChannel.of("live"))
        assertEquals(ServerChannel.Lab, ServerChannel.of("lab"))
        assertEquals(ServerChannel.Lab, ServerChannel.of(" LAB "))
        assertEquals(ServerChannel.Other("dev"), ServerChannel.of("dev"))
    }

    @Test fun aBlankEnvironmentIsUnknown() {
        assertNull(ServerChannel.of(null))
        assertNull(ServerChannel.of(""))
        assertNull(ServerChannel.of("  "))
    }

    @Test fun readsTheEnvironmentFromHealthz() {
        assertEquals(
            ServerChannel.Lab,
            ServerChannel.fromHealthz("""{"status": "ok", "environment": "lab", "version": "0.12.0", "commit": "abc1234"}"""),
        )
        assertEquals(ServerChannel.Live, ServerChannel.fromHealthz("""{"status": "ok", "environment": "live"}"""))
    }

    @Test fun healthzWithoutAnEnvironmentIsUnknown() {
        // An older server, an empty or null environment, a failing check, a captive portal, not an object.
        for (body in listOf(
            """{"status": "ok"}""",
            """{"status": "ok", "environment": ""}""",
            """{"status": "ok", "environment": null}""",
            """{"status": "down", "environment": "live"}""",
            "<html>Sign in to Wi-Fi</html>",
            """["ok"]""",
            "",
        )) {
            assertNull(body, ServerChannel.fromHealthz(body))
        }
    }

    @Test fun tags() {
        assertEquals("LIVE", ServerChannel.Live.tag)
        assertEquals("LAB", ServerChannel.Lab.tag)
        assertEquals("STAGING", ServerChannel.Other("staging").tag)
    }

    /** Instrument channel tokens: red for live, amber for lab, grey for anything else. */
    @Test fun colors() {
        assertEquals(R.color.inst_channel_live, ServerChannel.Live.color)
        assertEquals(R.color.inst_channel_lab, ServerChannel.Lab.color)
        assertEquals(R.color.inst_channel_other, ServerChannel.Other("dev").color)
    }
}
