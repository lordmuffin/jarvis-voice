package com.lordmuffin.jarvisvoice.live

import java.time.LocalDateTime
import java.time.format.DateTimeFormatter

/** Decides what the driving shortcut / tile does. Pure, so it is unit-tested without Android. */
object DrivingTrigger {
    enum class Action { START, START_LOCAL, MARK, OPEN_RECORD }

    fun decide(active: Boolean, micGranted: Boolean, configured: Boolean): Action = when {
        active -> Action.MARK
        !micGranted -> Action.OPEN_RECORD
        !configured -> Action.START_LOCAL
        else -> Action.START
    }

    fun title(now: LocalDateTime): String = "Drive " + now.format(FORMAT)

    private val FORMAT = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm")
}
