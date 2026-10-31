package com.lordmuffin.jarvisvoice.wear.tile

import androidx.wear.protolayout.LayoutElementBuilders
import org.junit.Test

/**
 * The tile is rebuilt with this timer while a recording runs. A build that throws takes the whole
 * process down with it, and the recording with the process.
 */
class RecordTileTimerTest {
    @Test fun `the running timer builds while a recording is active`() {
        RecordTileService.timerText(1_000L, LayoutElementBuilders.FontStyle.Builder().build())
    }
}
