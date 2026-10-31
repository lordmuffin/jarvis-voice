package com.lordmuffin.jarvisvoice.ui.compose

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectHorizontalDragGestures
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.withStyle
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import com.lordmuffin.jarvisvoice.R
import com.lordmuffin.jarvisvoice.live.ActionsUi
import com.lordmuffin.jarvisvoice.live.AudioUi
import com.lordmuffin.jarvisvoice.live.DetailTab
import com.lordmuffin.jarvisvoice.live.DetailUi
import com.lordmuffin.jarvisvoice.live.MarkdownModel
import com.lordmuffin.jarvisvoice.live.SummaryUi
import instrument.Inst

class DetailActions(
    val onTab: (DetailTab) -> Unit = {},
    val onRetry: () -> Unit = {},
    val onRefresh: () -> Unit = {},
    val onPlay: (String) -> Unit = {},
    val onSeek: (String, Float) -> Unit = { _, _ -> },
    val onProcess: () -> Unit = {},
    val onCopy: (String) -> Unit = {},
    val onDelete: () -> Unit = {},
    val onConfirmDelete: () -> Unit = {},
    val onCancelDelete: () -> Unit = {},
    val onRestore: () -> Unit = {},
)

/**
 * A session's detail: title, state and meta, its recordings, and Summary / Transcript / Actions.
 * Used full-screen on the cover display and as the right pane beside the history list when unfolded.
 */
@Composable
fun SessionDetailContent(ui: DetailUi, actions: DetailActions, modifier: Modifier = Modifier) {
    val c = Inst.colors
    val sp = InstUi.space
    Column(modifier.fillMaxSize().background(c.bg)) {
        when {
            ui.error != null -> Box(Modifier.padding(sp.s16)) {
                StatePanel(
                    stringResource(R.string.ui_detail_error_title), ui.error,
                    tone = StateTone.Error, actionText = stringResource(R.string.history_retry), onAction = actions.onRetry,
                )
            }
            !ui.loaded -> DetailSkeleton(ui)
            else -> Column(Modifier.fillMaxSize().refreshing(ui.refreshing)) {
                DetailHeader(ui, actions)
                if (ui.notice != null) {
                    Box(Modifier.fillMaxWidth().background(c.warningTint).padding(horizontal = sp.s16, vertical = sp.s8)) {
                        InstText(ui.notice, InstUi.type.body, color = c.warning)
                    }
                }
                if (ui.audio.isNotEmpty()) {
                    Column(Modifier.padding(horizontal = sp.s16, vertical = sp.s8), verticalArrangement = Arrangement.spacedBy(sp.s4)) {
                        ui.audio.forEach { AudioPlayerRow(it, actions) }
                    }
                }
                Box(Modifier.padding(horizontal = sp.s16, vertical = sp.s8)) {
                    InstSegmented(
                        options = listOf(
                            DetailTab.SUMMARY to stringResource(R.string.detail_tab_summary),
                            DetailTab.TRANSCRIPT to stringResource(R.string.detail_tab_transcript),
                            DetailTab.ACTIONS to stringResource(R.string.detail_tab_actions),
                        ),
                        selected = ui.tab, onSelect = actions.onTab,
                    )
                }
                DetailBody(ui, actions, Modifier.weight(1f))
            }
        }
    }
}

@Composable
private fun DetailSkeleton(ui: DetailUi) {
    val sp = InstUi.space
    Column(Modifier.fillMaxSize().padding(sp.s16), verticalArrangement = Arrangement.spacedBy(sp.s8)) {
        InstText(ui.title, InstUi.type.bodyLg.copy(fontWeight = FontWeight.Bold), maxLines = 2)
        SkeletonBlock(Modifier.fillMaxWidth().height(sp.controlMd))
        SkeletonBlock(Modifier.fillMaxWidth().height(sp.touchTarget))
        SkeletonBlock(Modifier.fillMaxWidth().height(sp.row * 4))
    }
}

@Composable
private fun DetailHeader(ui: DetailUi, actions: DetailActions) {
    val sp = InstUi.space
    Column(Modifier.fillMaxWidth().padding(horizontal = sp.s16, vertical = sp.s12), verticalArrangement = Arrangement.spacedBy(sp.s4)) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(sp.s8)) {
            InstText(ui.title, InstUi.type.bodyLg.copy(fontWeight = FontWeight.Bold), Modifier.weight(1f), maxLines = 3)
            ui.status?.let { InstChip(it, ui.statusTone.color()) }
            InstButton(stringResource(R.string.ui_refresh), actions.onRefresh, kind = ButtonKind.Quiet)
        }
        // The server's release channel (a red LIVE tag) stays in the app bar: beside a finished
        // session's meta it read as the session's own state.
        InstText(ui.meta, InstUi.type.meta)
        if (ui.deleted) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(sp.s8)) {
                InstText(
                    stringResource(R.string.detail_deleted_notice, ui.trashNote.orEmpty()), InstUi.type.caption,
                    Modifier.weight(1f), color = Inst.colors.warning, maxLines = 3,
                )
                InstButton(stringResource(R.string.detail_restore), actions.onRestore, size = ButtonSize.Sm, enabled = !ui.deleteBusy)
            }
        } else if (ui.confirmingDelete) {
            Column(verticalArrangement = Arrangement.spacedBy(sp.s8)) {
                InstText(stringResource(R.string.detail_delete_confirm_title), InstUi.type.body.copy(fontWeight = FontWeight.Bold))
                InstText(stringResource(R.string.detail_delete_confirm_line), InstUi.type.caption, maxLines = 4)
                Row(horizontalArrangement = Arrangement.spacedBy(sp.s8)) {
                    InstButton(stringResource(R.string.detail_delete_cancel), actions.onCancelDelete, kind = ButtonKind.Quiet, size = ButtonSize.Sm, enabled = !ui.deleteBusy)
                    InstButton(stringResource(R.string.detail_delete_confirm), actions.onConfirmDelete, kind = ButtonKind.Danger, size = ButtonSize.Sm, enabled = !ui.deleteBusy)
                }
            }
        } else if (ui.canDelete) {
            InstButton(stringResource(R.string.detail_delete), actions.onDelete, kind = ButtonKind.Quiet, size = ButtonSize.Sm)
        }
        if (!ui.deleted && (ui.canProcess || ui.processing != null)) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(sp.s8)) {
                if (ui.canProcess) {
                    InstButton(
                        stringResource(R.string.detail_process), actions.onProcess,
                        size = ButtonSize.Sm, enabled = !ui.processBusy,
                    )
                }
                ui.processing?.let { InstChip(it, ui.processingTone.color(), leading = if (ui.processBusy) "◌" else null) }
            }
        }
    }
}

@Composable
private fun DetailBody(ui: DetailUi, actions: DetailActions, modifier: Modifier) {
    val sp = InstUi.space
    when (ui.tab) {
        DetailTab.SUMMARY -> Column(modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(sp.s16)) {
            ui.summaryText?.let { text ->
                InstButton(stringResource(R.string.detail_copy_summary), { actions.onCopy(text) }, kind = ButtonKind.Quiet, size = ButtonSize.Sm)
            }
            when (val s = ui.summary) {
                is SummaryUi.Note -> MarkdownBlocks(s.blocks)
                is SummaryUi.Placeholder -> Placeholder(s.text)
            }
        }
        DetailTab.TRANSCRIPT ->
            if (ui.transcript.isEmpty()) Column(modifier.padding(sp.s16)) {
                StatePanel(stringResource(R.string.ui_transcript_empty_title), stringResource(R.string.detail_transcript_empty))
            } else LazyColumn(
                modifier.fillMaxWidth(),
                contentPadding = PaddingValues(sp.s16),
                verticalArrangement = Arrangement.spacedBy(sp.s4),
            ) { items(ui.transcript, key = { it.id }) { UtteranceRow(it) } }
        DetailTab.ACTIONS -> Column(modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(sp.s16), verticalArrangement = Arrangement.spacedBy(sp.s16)) {
            ui.actionsText?.let { text ->
                InstButton(stringResource(R.string.detail_copy_actions), { actions.onCopy(text) }, kind = ButtonKind.Quiet, size = ButtonSize.Sm)
            }
            when (val a = ui.actions) {
                is ActionsUi.Placeholder -> Placeholder(a.text)
                is ActionsUi.Sections -> a.sections.forEach { section ->
                    Column(verticalArrangement = Arrangement.spacedBy(sp.s8)) {
                        SectionLabel(section.title)
                        section.items.forEach { item ->
                            Row(horizontalArrangement = Arrangement.spacedBy(sp.s8)) {
                                InstText(section.glyph, InstUi.type.body, color = Inst.colors.accent)
                                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(sp.s4 / 2)) {
                                    InstText(item.text, InstUi.type.body)
                                    if (item.extra != null) InstText(item.extra, InstUi.type.meta)
                                }
                                item.copyText?.let { line ->
                                    InstButton(
                                        stringResource(R.string.detail_copy_item), { actions.onCopy(line) },
                                        kind = ButtonKind.Quiet, size = ButtonSize.Sm,
                                        contentDescription = stringResource(R.string.detail_copy_item_a11y, item.text),
                                    )
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun Placeholder(text: String) {
    InstText(text, InstUi.type.caption, color = Inst.colors.text2)
}

private fun inline(inlines: List<MarkdownModel.Inline>, code: SpanStyle): AnnotatedString = buildAnnotatedString {
    for (i in inlines) {
        when {
            i.code -> withStyle(code) { append(i.text) }
            i.bold -> withStyle(SpanStyle(fontWeight = FontWeight.Bold)) { append(i.text) }
            else -> append(i.text)
        }
    }
}

/** The note's Markdown subset (`MarkdownModel`) drawn with Instrument type. */
@Composable
fun MarkdownBlocks(blocks: List<MarkdownModel.Block>) {
    val sp = InstUi.space
    val c = Inst.colors
    val code = SpanStyle(fontFamily = FontFamily.Monospace, background = c.surface2)
    Column(verticalArrangement = Arrangement.spacedBy(sp.s8)) {
        for (b in blocks) when (b) {
            is MarkdownModel.Block.Heading -> BasicHeading(inline(b.inlines, code), b.level)
            is MarkdownModel.Block.Paragraph -> androidx.compose.foundation.text.BasicText(inline(b.inlines, code), style = InstUi.type.body)
            is MarkdownModel.Block.Bullet -> Row(Modifier.padding(start = sp.s16 * b.depth), horizontalArrangement = Arrangement.spacedBy(sp.s8)) {
                InstText("•", InstUi.type.body, color = c.accent)
                androidx.compose.foundation.text.BasicText(inline(b.inlines, code), Modifier.weight(1f), style = InstUi.type.body)
            }
            is MarkdownModel.Block.Task -> Row(Modifier.padding(start = sp.s16 * b.depth), horizontalArrangement = Arrangement.spacedBy(sp.s8)) {
                InstText(if (b.checked) "✓" else "☐", InstUi.type.body, color = c.accent)
                androidx.compose.foundation.text.BasicText(inline(b.inlines, code), Modifier.weight(1f), style = InstUi.type.body)
            }
            MarkdownModel.Block.Rule -> Hairline(Modifier.fillMaxWidth().height(sp.hairline))
        }
    }
}

@Composable
private fun BasicHeading(text: AnnotatedString, level: Int) {
    val base = if (level <= 2) InstUi.type.bodyLg else InstUi.type.bodyStrong
    androidx.compose.foundation.text.BasicText(text, Modifier.padding(top = InstUi.space.s4), style = base.copy(fontWeight = FontWeight.Bold))
}

/** A recording: 48dp play button, who it is, a seek track and the time. */
@Composable
private fun AudioPlayerRow(a: AudioUi, actions: DetailActions) {
    val c = Inst.colors
    val sp = InstUi.space
    val who = stringResource(if (a.speaker == com.lordmuffin.jarvisvoice.live.protocol.Speaker.ME) R.string.detail_audio_mic else R.string.detail_audio_system)
    Row(
        Modifier.fillMaxWidth().background(c.surface).padding(end = sp.s12),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(sp.s8),
    ) {
        InstButton(
            stringResource(if (a.playing) R.string.detail_pause else R.string.detail_play),
            { actions.onPlay(a.channel) },
            enabled = !a.busy,
            contentDescription = stringResource(R.string.ui_audio_play_a11y, who),
        )
        SpeakerTag(a.speaker)
        InstSeekBar(a.progress, a.seekable, { actions.onSeek(a.channel, it) }, Modifier.weight(1f))
        InstText(a.time, InstUi.type.metaMono, maxLines = 1)
    }
}

/** A thin track with a filled part; tap or drag to seek while the recording is playing. */
@Composable
fun InstSeekBar(progress: Float, enabled: Boolean, onSeek: (Float) -> Unit, modifier: Modifier = Modifier) {
    val c = Inst.colors
    val sp = InstUi.space
    var width by remember { mutableIntStateOf(1) }
    Canvas(
        modifier
            .heightIn(min = sp.touchTarget).height(sp.touchTarget)
            .onSizeChanged { width = it.width.coerceAtLeast(1) }
            .pointerInput(enabled) {
                if (!enabled) return@pointerInput
                detectTapGestures { onSeek(it.x / width) }
            }
            .pointerInput(enabled) {
                if (!enabled) return@pointerInput
                detectHorizontalDragGestures { change, _ -> onSeek(change.position.x / width) }
            },
    ) {
        val h = sp.s4.toPx()
        val y = (size.height - h) / 2
        drawRect(c.surface2, Offset(0f, y), Size(size.width, h))
        drawRect(if (enabled) c.accent else c.control, Offset(0f, y), Size(size.width * progress.coerceIn(0f, 1f), h))
    }
}


/** The detail as its own screen (cover display): back row, the content, and the main navigation. */
@Composable
fun SessionDetailScreen(
    ui: DetailUi,
    actions: DetailActions,
    onBack: () -> Unit,
    onNav: (com.lordmuffin.jarvisvoice.BottomNav.Tab) -> Unit,
    modifier: Modifier = Modifier,
) {
    val wide = androidx.compose.ui.res.booleanResource(R.bool.is_wide)
    Column(modifier.fillMaxSize().background(Inst.colors.bg)) {
        InstAppBar(stringResource(R.string.history_title), channel = ui.channel)
        if (wide) {
            Row(Modifier.weight(1f).fillMaxWidth()) {
                MainNavRail(null, onNav)
                Column(Modifier.weight(1f)) {
                    InstButton(stringResource(R.string.ui_back_history), onBack, Modifier.padding(horizontal = InstUi.space.s8), kind = ButtonKind.Quiet)
                    SessionDetailContent(ui, actions, Modifier.weight(1f))
                }
            }
        } else {
            InstButton(stringResource(R.string.ui_back_history), onBack, Modifier.padding(horizontal = InstUi.space.s8), kind = ButtonKind.Quiet)
            SessionDetailContent(ui, actions, Modifier.weight(1f))
            MainNavBar(null, onNav)
        }
    }
}
