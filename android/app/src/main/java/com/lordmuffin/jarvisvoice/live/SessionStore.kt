package com.lordmuffin.jarvisvoice.live

import com.lordmuffin.jarvisvoice.live.protocol.Channel
import com.lordmuffin.jarvisvoice.live.protocol.CopilotSnapshot
import com.lordmuffin.jarvisvoice.live.protocol.DraftSegment
import com.lordmuffin.jarvisvoice.live.protocol.Segment
import com.lordmuffin.jarvisvoice.live.protocol.ServerEvent
import com.lordmuffin.jarvisvoice.live.protocol.Speaker

/** One row of the live transcript. */
data class TranscriptLine(
    val id: String,
    val channel: Channel,
    val speaker: Speaker,
    val startMs: Long,
    val endMs: Long,
    val text: String,
    /** `true` for on-device drafts; `false` once the server's authoritative segment arrived. */
    val isDraft: Boolean,
    val final: Boolean = !isDraft,
    val sttTier: String? = null,
) {
    /** Whether two lines on the same channel cover overlapping time. */
    fun overlaps(other: TranscriptLine): Boolean {
        if (channel != other.channel) return false
        if (startMs == other.startMs && endMs == other.endMs) return true
        return startMs < other.endMs && other.startMs < endMs
    }
}

/** Immutable view of a session for the UI. */
data class SessionSnapshot(
    val lines: List<TranscriptLine> = emptyList(),
    val copilot: CopilotSnapshot? = null,
    val sttTier: String? = null,
    val lagMs: Long = 0,
    val llmOk: Boolean = true,
    val finalNote: ServerEvent.FinalNote? = null,
    val lastError: ServerEvent.Error? = null,
)

/**
 * Transcript and copilot state for one live session — a port of the Mac's `SessionStore`.
 * Drafts show immediately; a server `segment` replaces every draft it overlaps.
 */
class SessionStore {
    private val lines = ArrayList<TranscriptLine>()
    private var draftCounter = 0
    @Volatile var snapshot = SessionSnapshot()
        private set

    @Synchronized
    fun apply(event: ServerEvent): SessionSnapshot {
        when (event) {
            is ServerEvent.SegmentEvent -> applyFinal(event.segment)
            is ServerEvent.Copilot -> {
                val current = snapshot.copilot
                if (current == null || event.snapshot.version > current.version) {
                    snapshot = snapshot.copy(copilot = event.snapshot)
                }
            }
            is ServerEvent.Status -> snapshot = snapshot.copy(
                sttTier = event.sttTier, lagMs = event.lagMs, llmOk = event.llmOk,
            )
            is ServerEvent.FinalNote -> snapshot = snapshot.copy(finalNote = event)
            is ServerEvent.Error -> snapshot = snapshot.copy(lastError = event)
            is ServerEvent.Ack, is ServerEvent.HelloAck -> Unit  // handled by the transport
        }
        return publish()
    }

    /** Shows a locally produced draft until the server confirms the same stretch of audio. */
    @Synchronized
    fun applyDraft(draft: DraftSegment): SessionSnapshot {
        draftCounter += 1
        val line = TranscriptLine(
            id = "draft-$draftCounter",
            channel = draft.channel,
            speaker = if (draft.channel == Channel.MIC) Speaker.ME else Speaker.THEM,
            startMs = draft.startMs,
            endMs = draft.endMs,
            text = draft.text,
            isDraft = true,
            final = draft.final,
        )
        // A draft that arrives after the server already settled that time span is stale.
        if (lines.any { !it.isDraft && it.overlaps(line) }) return snapshot
        // A newer draft supersedes earlier drafts of the same utterance.
        lines.removeAll { it.isDraft && it.overlaps(line) }
        insert(line)
        return publish()
    }

    /** Final on-device drafts, for the local Markdown file. */
    @Synchronized
    fun finalDrafts(): List<TranscriptLine> = lines.filter { it.isDraft && it.final }

    private fun applyFinal(segment: Segment) {
        val line = TranscriptLine(
            id = segment.id,
            channel = segment.channel,
            speaker = segment.speaker,
            startMs = segment.startMs,
            endMs = segment.endMs,
            text = segment.text,
            isDraft = false,
            sttTier = segment.sttTier,
        )
        lines.removeAll { it.id == line.id || (it.isDraft && it.overlaps(line)) }
        insert(line)
    }

    /** Keeps lines ordered by start, end, channel, id so equal timestamps never reorder. */
    private fun insert(line: TranscriptLine) {
        val index = lines.indexOfFirst { ORDER.compare(it, line) > 0 }
        if (index < 0) lines.add(line) else lines.add(index, line)
    }

    private fun publish(): SessionSnapshot {
        snapshot = snapshot.copy(lines = lines.toList())
        return snapshot
    }

    private companion object {
        val ORDER = compareBy<TranscriptLine>({ it.startMs }, { it.endMs }, { it.channel.index }, { it.id })
    }
}
