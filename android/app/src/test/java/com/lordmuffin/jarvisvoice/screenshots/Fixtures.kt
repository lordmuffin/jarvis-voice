package com.lordmuffin.jarvisvoice.screenshots

import com.lordmuffin.jarvisvoice.live.ActionItemUi
import com.lordmuffin.jarvisvoice.live.ActionSectionUi
import com.lordmuffin.jarvisvoice.live.ActionsUi
import com.lordmuffin.jarvisvoice.live.AudioUi
import com.lordmuffin.jarvisvoice.live.ConnectionKind
import com.lordmuffin.jarvisvoice.live.DetailTab
import com.lordmuffin.jarvisvoice.live.DetailUi
import com.lordmuffin.jarvisvoice.live.MarkdownModel
import com.lordmuffin.jarvisvoice.live.SummaryUi
import com.lordmuffin.jarvisvoice.live.ServerChannel
import com.lordmuffin.jarvisvoice.live.protocol.SessionMode
import com.lordmuffin.jarvisvoice.live.protocol.Speaker
import com.lordmuffin.jarvisvoice.ui.compose.CopilotBlock
import com.lordmuffin.jarvisvoice.ui.compose.DictationUi
import com.lordmuffin.jarvisvoice.ui.compose.FeedLine
import com.lordmuffin.jarvisvoice.ui.compose.PendingUi
import com.lordmuffin.jarvisvoice.ui.compose.RecentRow
import com.lordmuffin.jarvisvoice.ui.compose.RecentUi
import com.lordmuffin.jarvisvoice.ui.compose.RecordPhase
import com.lordmuffin.jarvisvoice.ui.compose.RecordUi
import com.lordmuffin.jarvisvoice.ui.compose.Tone

/** Sample data shared by the screenshot tests. The same sessions appear on every screen. */
object Fixtures {
    val recentRows = listOf(
        RecentRow("s1", "ARC runner migration vendor call", "Live", Tone.Accent, "Oct 14, 2026, 3:05 PM · in progress · Meeting"),
        RecentRow("s2", "Weekly sync", "Done", Tone.Success, "Oct 11, 2026, 9:14 AM · 42:10 · Meeting"),
        RecentRow("s3", "Harbor cache review", "Finalizing", Tone.Warning, "Oct 10, 2026, 4:30 PM · 18:02 · Solo"),
        RecentRow("s4", "Standup notes", "Failed", Tone.Error, "Oct 9, 2026, 9:00 AM · 03:15 · Solo"),
        RecentRow("s5", "Rack cabling walk-through", "Done", Tone.Success, "Oct 8, 2026, 1:20 PM · 27:44 · Solo"),
    )

    val pending = listOf(
        PendingUi("p1", "Standup notes", "Recorded locally · Oct 14, 2026, 9:02 AM", Tone.Neutral, canUpload = true),
        PendingUi("p2", "Rack cabling walk-through", "Interrupted · resuming upload", Tone.Warning, canUpload = true),
        PendingUi("p3", null, "Failed: the server was unreachable.", Tone.Error, canUpload = true),
    )

    val idle = RecordUi(
        phase = RecordPhase.IDLE,
        serverLine = "Server live.lab.apj.dev",
        channel = ServerChannel.Lab,
        connection = ConnectionKind.IDLE,
        dictation = DictationUi(true, "Last: 42 words · 138 wpm · Enhanced · 2 min ago"),
        mode = SessionMode.MEETING,
        networkLabel = "Home network · streaming to Jarvis Live",
        pending = pending,
        recent = RecentUi.Items(recentRows),
    )

    val transcript = listOf(
        FeedLine("1", "14:32", Speaker.ME, "What's the timeline for the ARM images?"),
        FeedLine("2", "14:51", Speaker.THEM, "ARM is in preview. I can't commit to a date on this call."),
        FeedLine("3", "15:40", Speaker.THEM, "We can support ARC scale sets, but GPU runners are a separate SKU."),
        FeedLine("4", "16:12", Speaker.ME, "Send me the GPU runner pricing and we'll size it against the DGX node."),
        FeedLine("5", "16:40", Speaker.THEM, "Sure. Can you send the node inventory so we can quote?"),
        FeedLine("6", "17:02", Speaker.ME, "I'll send it by Friday."),
        FeedLine("7", "17:30", Speaker.ME, "Let's pilot on the k3s2 cluster first and keep the VM runners as a fallback."),
        FeedLine("8", "18:05", Speaker.THEM, "That works. We'd want read access to the cluster metrics during the pilot."),
        FeedLine("9", "18:31", Speaker.ME, "Metrics are fine, but nothing with write access to the", draft = true),
    )

    val levels = listOf(
        .05f, .1f, .3f, .55f, .8f, .6f, .35f, .2f, .1f, .25f, .5f, .9f, .7f, .45f, .3f, .15f, .1f, .35f, .6f, .85f,
        .65f, .4f, .2f, .12f, .08f, .3f, .55f, .75f, .5f, .3f, .15f, .1f, .4f, .7f, .95f, .6f, .3f, .2f, .1f, .05f,
    )

    val copilot = listOf(
        CopilotBlock("Suggestions", listOf("Question: Ask whether ARM images are on the public roadmap.", "Gap: You haven't discussed egress costs.")),
        CopilotBlock("Actions", listOf("Confirm GPU runner pricing. (Vendor)", "Send the node inventory. (Me) due Fri 16 Oct")),
        CopilotBlock("Decisions", listOf("Pilot on the k3s2 cluster first.")),
    )

    val recording = idle.copy(
        phase = RecordPhase.RECORDING,
        connection = ConnectionKind.CONNECTED,
        connectionDetail = "STT pve · lag 840 ms · 0.0 s queued",
        sessionTitle = "ARC runner migration vendor call",
        details = "Meeting · Drafts on · 2 markers",
        timer = "18:42",
        levels = levels,
        transcript = transcript,
        copilot = copilot,
    )

    private val note = MarkdownModel.parse(
        """
        ## Summary
        The vendor can support **ARC scale sets** today, but GPU runners are a separate SKU and ARM images are still in preview.

        ## Decisions
        - Pilot on the k3s2 cluster first, keep the VM runners as a fallback.
        - Read-only metrics access for the vendor during the pilot.

        ## Follow-ups
        - [ ] Send the node inventory by Friday
        - [ ] Confirm GPU runner pricing
        - [x] Ask about the ARM image roadmap
        """.trimIndent()
    )

    val detail = DetailUi(
        title = "ARC runner migration vendor call",
        status = "Done",
        statusTone = Tone.Success,
        meta = "Oct 14, 2026, 3:05 PM · 30:04 · Meeting",
        channel = ServerChannel.Lab,
        loading = false,
        tab = DetailTab.SUMMARY,
        summary = SummaryUi.Note(note),
        transcript = transcript.map { it.copy(draft = false) },
        actions = ActionsUi.Sections(
            listOf(
                ActionSectionUi("Actions", "☐", listOf(ActionItemUi("Send the node inventory.", "owner: Me · due Fri 16 Oct"), ActionItemUi("Confirm GPU runner pricing.", "owner: Vendor"))),
                ActionSectionUi("Decisions", "✓", listOf(ActionItemUi("Pilot on the k3s2 cluster first.", null))),
                ActionSectionUi("Notes", "•", listOf(ActionItemUi("No date yet for the ARM images.", null))),
            )
        ),
        audio = listOf(
            AudioUi("mic", Speaker.ME, time = "00:00 / 30:04"),
            AudioUi("system", Speaker.THEM, playing = true, time = "14:02 / 30:04", progress = 0.47f, seekable = true),
        ),
        canProcess = true,
    )

    /** "Transcribe & summarize" waiting while a job (this button's or the server's own) runs. */
    val detailProcessing = detail.copy(processBusy = true, processing = "Transcribing…")

    val detailFinalizing = detail.copy(
        status = "Finalizing", statusTone = Tone.Warning,
        notice = "The note is still being prepared.",
        summary = SummaryUi.Placeholder("The summary appears here when the session is finalized."),
        audio = emptyList(),
    )

    val detailLoading = DetailUi(title = "Weekly sync")
    val detailError = DetailUi(title = "Weekly sync", loading = false, error = "Couldn't load this session: HTTP 502")

    val sessions = com.lordmuffin.jarvisvoice.ui.compose.SessionsUi(
        subtitle = "Your Jarvis Live sessions on live.lab.apj.dev",
        channel = ServerChannel.Lab,
        list = RecentUi.Items(recentRows),
        footer = com.lordmuffin.jarvisvoice.ui.compose.ListFooter.MORE,
    )
}
