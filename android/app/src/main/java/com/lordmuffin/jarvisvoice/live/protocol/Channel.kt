package com.lordmuffin.jarvisvoice.live.protocol

/** Audio channel as named on the wire (`protocol/v1`). The phone only ever records [MIC]. */
enum class Channel(val wire: String, val index: Int) {
    MIC("mic", 0),
    SYSTEM("system", 1);

    companion object {
        fun fromWire(value: String): Channel? = entries.firstOrNull { it.wire == value }
        fun fromIndex(value: Int): Channel? = entries.firstOrNull { it.index == value }
    }
}

enum class SessionMode(val wire: String) {
    SOLO("solo"),
    MEETING("meeting");

    companion object {
        fun fromWire(value: String): SessionMode? = entries.firstOrNull { it.wire == value }
    }
}

enum class Speaker(val wire: String) {
    ME("me"),
    THEM("them");

    companion object {
        fun fromWire(value: String): Speaker? = entries.firstOrNull { it.wire == value }
    }
}
