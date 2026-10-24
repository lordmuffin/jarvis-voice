package com.lordmuffin.jarvisvoice.ui.compose

import android.content.Context
import android.provider.Settings
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.interaction.collectIsDraggedAsState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.scale
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.dimensionResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontStyle
import com.lordmuffin.jarvisvoice.R
import com.lordmuffin.jarvisvoice.live.protocol.Speaker
import instrument.Inst

/** True when the user turned animations off; ambient motion then holds its static state. */
@Composable
fun rememberReducedMotion(): Boolean {
    val context = LocalContext.current
    return remember(context) { reducedMotion(context) }
}

private fun reducedMotion(context: Context): Boolean = runCatching {
    Settings.Global.getFloat(context.contentResolver, Settings.Global.ANIMATOR_DURATION_SCALE, 1f) == 0f
}.getOrDefault(false)

/**
 * The live input waveform: the most recent levels as bars, newest on the right, the line growing
 * left to right as the session starts. A silent sample still draws a hairline so the strip reads
 * as listening.
 */
@Composable
fun LiveWaveform(levels: List<Float>, modifier: Modifier = Modifier, color: Color = Inst.colors.accent) {
    val barW = dimensionResource(R.dimen.app_waveform_bar)
    val gap = InstUi.space.s4 / 2
    val floor = InstUi.space.hairline
    Canvas(modifier.height(dimensionResource(R.dimen.app_waveform_height))) {
        val step = (barW + gap).toPx()
        val bar = barW.toPx()
        val capacity = ((size.width + gap.toPx()) / step).toInt().coerceAtLeast(1)
        val shown = levels.takeLast(capacity)
        val minH = floor.toPx()
        shown.forEachIndexed { i, level ->
            val h = (size.height * level).coerceAtLeast(minH)
            // Right-aligned: the newest sample sits at the end of the strip.
            val x = size.width - (shown.size - i) * step + gap.toPx()
            drawRoundRect(
                color = color,
                topLeft = Offset(x, (size.height - h) / 2),
                size = Size(bar, h),
                cornerRadius = CornerRadius(bar / 2),
            )
        }
    }
}

/** The recording status (`i-live`): breathing dot, REC, elapsed time, and the live waveform. */
@Composable
fun RecPill(time: String, levels: List<Float>, modifier: Modifier = Modifier) {
    val c = Inst.colors
    val sp = InstUi.space
    val reduced = rememberReducedMotion()
    val scale = if (reduced) 1f else {
        val t = rememberInfiniteTransition(label = "breathe")
        t.animateFloat(
            initialValue = 1f, targetValue = 1.4f,
            animationSpec = infiniteRepeatable(tween(durationMillis = 1400, easing = LinearEasing), RepeatMode.Reverse),
            label = "dot",
        ).value
    }
    val a11y = stringResource(R.string.ui_rec_a11y, time)
    Row(
        modifier
            .background(c.surface2, InstUi.shapes.pill)
            .padding(horizontal = sp.s16)
            .height(sp.controlLg)
            .semantics(mergeDescendants = true) { contentDescription = a11y },
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(sp.s8),
    ) {
        Box(Modifier.size(sp.dotLive).scale(scale).background(c.error, InstUi.shapes.round))
        InstText(stringResource(R.string.ui_rec), InstUi.type.tag, color = c.error)
        InstText(time, InstUi.type.metaMono, color = c.text)
        LiveWaveform(levels, Modifier.weight(1f), color = c.accent)
    }
}

/** Me is accent-filled, Them violet-filled, each with its on-colour text. */
@Composable
fun SpeakerTag(speaker: Speaker, modifier: Modifier = Modifier) {
    val c = Inst.colors
    val me = speaker == Speaker.ME
    Box(
        modifier
            .height(dimensionResource(R.dimen.inst_badge))
            .background(if (me) c.accent else c.them)
            .padding(horizontal = InstUi.space.s8 - InstUi.space.s4 / 2),
        contentAlignment = Alignment.Center,
    ) {
        InstText(
            stringResource(if (me) R.string.detail_speaker_me else R.string.detail_speaker_them).uppercase(),
            InstUi.type.tag, color = if (me) c.onAccent else c.onThem, maxLines = 1,
        )
    }
}

/** One line of a transcript, as the feed and the detail pane show it. */
data class FeedLine(
    val id: String,
    val time: String,
    val speaker: Speaker,
    val text: String,
    val draft: Boolean = false,
)

/** A surface block with a 3dp rule on the left: accent for you, violet for them (`i-utt`). */
@Composable
fun UtteranceRow(line: FeedLine, modifier: Modifier = Modifier) {
    val c = Inst.colors
    val sp = InstUi.space
    val rule = if (line.speaker == Speaker.ME) c.accent else c.them
    Row(modifier.fillMaxWidth().height(IntrinsicSize.Min).background(c.surface)) {
        Box(Modifier.width(sp.railEdge).fillMaxHeight().background(rule))
        Column(Modifier.weight(1f).padding(sp.s12), verticalArrangement = Arrangement.spacedBy(sp.s4)) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(sp.s8)) {
                SpeakerTag(line.speaker)
                InstText(line.time, InstUi.type.metaMono)
            }
            InstText(
                line.text,
                InstUi.type.chat.copy(fontStyle = if (line.draft) FontStyle.Italic else FontStyle.Normal),
                color = if (line.draft) c.text2 else c.text,
            )
        }
    }
}

/**
 * The transcript feed. Newest line at the bottom, pinned there while you are following: it scrolls
 * itself as lines arrive and as a draft line grows. Dragging up pauses it and offers "Jump to live";
 * reaching the end again, or tapping that, resumes (`FollowLive`).
 */
@Composable
fun TranscriptFeed(
    lines: List<FeedLine>,
    modifier: Modifier = Modifier,
    empty: @Composable () -> Unit = {},
    contentPadding: PaddingValues = PaddingValues(),
    onJumpToLive: (() -> Unit)? = null,
) {
    val sp = InstUi.space
    val state = rememberLazyListState()
    val follow = remember { FollowLive() }
    var following by remember { mutableStateOf(true) }
    // The list is reversed (index 0 = newest, drawn at the bottom), so "at the end" is index 0.
    val atEnd by remember { derivedStateOf { state.firstVisibleItemIndex == 0 && state.firstVisibleItemScrollOffset == 0 } }
    val dragging by state.interactionSource.collectIsDraggedAsState()
    LaunchedEffect(atEnd, dragging) {
        follow.onScroll(atEnd, dragging)
        following = follow.following
    }
    val newest = lines.lastOrNull()
    LaunchedEffect(lines.size, newest?.text, following) {
        if (following && lines.isNotEmpty()) state.scrollToItem(0)
    }
    Box(modifier) {
        if (lines.isEmpty()) {
            empty()
        } else {
            LazyColumn(
                state = state,
                reverseLayout = true,
                modifier = Modifier.fillMaxSize(),
                contentPadding = contentPadding,
                verticalArrangement = Arrangement.spacedBy(sp.s4),
            ) {
                itemsIndexed(lines.asReversed(), key = { _, l -> l.id }) { _, line -> UtteranceRow(line) }
            }
            if (!following) {
                InstButton(
                    stringResource(R.string.ui_jump_to_live),
                    onClick = { follow.jumpToLive(); following = true; onJumpToLive?.invoke() },
                    modifier = Modifier.align(Alignment.BottomCenter).padding(bottom = sp.s8),
                    kind = ButtonKind.Primary, size = ButtonSize.Sm,
                )
            }
        }
    }
}
