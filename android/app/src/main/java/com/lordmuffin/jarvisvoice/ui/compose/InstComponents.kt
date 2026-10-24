package com.lordmuffin.jarvisvoice.ui.compose

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.selectableGroup
import androidx.compose.foundation.text.BasicText
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.res.dimensionResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import com.lordmuffin.jarvisvoice.R
import com.lordmuffin.jarvisvoice.live.ServerChannel
import instrument.Inst
import instrument.InstChannel

/** Text in an Instrument style; [color] overrides the style's own. */
@Composable
fun InstText(
    text: String,
    style: TextStyle,
    modifier: Modifier = Modifier,
    color: Color = Color.Unspecified,
    maxLines: Int = Int.MAX_VALUE,
    overflow: TextOverflow = TextOverflow.Clip,
    textAlign: TextAlign? = null,
) {
    var resolved = if (color == Color.Unspecified) style else style.copy(color = color)
    if (textAlign != null) resolved = resolved.copy(textAlign = textAlign)
    BasicText(text = text, modifier = modifier, style = resolved, maxLines = maxLines, overflow = overflow)
}

enum class ButtonKind { Primary, Secondary, Quiet, Danger }
enum class ButtonSize { Sm, Md, Lg }

/**
 * Instrument pill button. The drawn pill is the token height (32 / 36 / 52dp); the tap area is never
 * smaller than 48dp. A disabled button is dashed and greyed, and the caller gives the reason beside it.
 */
@Composable
fun InstButton(
    text: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    kind: ButtonKind = ButtonKind.Secondary,
    size: ButtonSize = ButtonSize.Md,
    enabled: Boolean = true,
    contentDescription: String? = null,
) {
    val c = Inst.colors
    val sp = InstUi.space
    val visual = when (size) { ButtonSize.Sm -> sp.controlSm; ButtonSize.Md -> sp.controlMd; ButtonSize.Lg -> sp.controlLg }
    val fill = when {
        !enabled -> c.surface2
        kind == ButtonKind.Primary -> c.accent
        kind == ButtonKind.Danger -> c.error
        kind == ButtonKind.Secondary -> c.surface2
        else -> Color.Transparent
    }
    val label = when {
        !enabled -> c.textDisabled
        kind == ButtonKind.Primary -> c.onAccent
        kind == ButtonKind.Danger -> c.onError
        kind == ButtonKind.Secondary -> c.text
        else -> c.text2
    }
    val outline = enabled && kind == ButtonKind.Secondary || !enabled
    val dashed = !enabled
    val control = c.control
    val stroke = sp.hairline
    val dash = sp.s4
    val gap = sp.s4 / 2
    Box(
        modifier
            .heightIn(min = sp.touchTarget)
            .widthIn(min = sp.touchTarget)
            .clip(InstUi.shapes.pill)
            .clickable(enabled = enabled, role = Role.Button, onClick = onClick)
            .semantics { if (contentDescription != null) this.contentDescription = contentDescription },
        contentAlignment = Alignment.Center,
        propagateMinConstraints = true, // a full-width button draws full width
    ) {
        Box(
            Modifier
                .height(visual)
                .background(fill, InstUi.shapes.pill)
                .drawBehind {
                    if (outline) {
                        val w = stroke.toPx()
                        val dim = this.size // the draw scope's size, not the button's [size] parameter
                        drawRoundRect(
                            color = control,
                            topLeft = Offset(w / 2, w / 2),
                            size = Size(dim.width - w, dim.height - w),
                            cornerRadius = CornerRadius(dim.height / 2),
                            style = Stroke(w, pathEffect = if (dashed) PathEffect.dashPathEffect(floatArrayOf(dash.toPx(), gap.toPx())) else null),
                        )
                    }
                }
                .padding(horizontal = if (kind == ButtonKind.Quiet) sp.s8 else sp.s16),
            contentAlignment = Alignment.Center,
        ) {
            InstText(text, InstUi.type.button, color = label, maxLines = 1)
        }
    }
}

/** A square status chip: surface-2, tag type, state colour on the text (`i-chip`). */
@Composable
fun InstChip(text: String, color: Color, modifier: Modifier = Modifier, leading: String? = null) {
    val sp = InstUi.space
    Row(
        modifier
            .heightIn(min = dimensionResource(R.dimen.inst_chip))
            .background(Inst.colors.surface2)
            .padding(horizontal = sp.s8 - sp.s4 / 2),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(sp.s4),
    ) {
        if (leading != null) InstText(leading, InstUi.type.tag, color = color)
        InstText(text.uppercase(), InstUi.type.tag, color = color, maxLines = 1)
    }
}

/** The server's release channel as a full-pill badge: red LIVE, amber LAB, grey otherwise. */
@Composable
fun ChannelBadge(channel: ServerChannel, modifier: Modifier = Modifier) {
    val fill = when (channel) {
        ServerChannel.Live -> InstChannel.channelLive
        ServerChannel.Lab -> InstChannel.channelLab
        is ServerChannel.Other -> InstChannel.channelOther
    }
    val sp = InstUi.space
    Box(
        modifier
            .height(dimensionResource(R.dimen.inst_badge))
            .background(fill, InstUi.shapes.round)
            .padding(horizontal = sp.s8),
        contentAlignment = Alignment.Center,
    ) { InstText(channel.tag, InstUi.type.tag, color = InstChannel.onChannel, maxLines = 1) }
}

/** A flat surface block (`i-block`): lists are stacks of these, separated by a thin gap of bg. */
@Composable
fun InstBlock(modifier: Modifier = Modifier, content: @Composable () -> Unit) {
    Box(modifier.background(Inst.colors.surface)) { content() }
}

enum class StateTone { Neutral, Error, Warning }

/**
 * The one template for empty, error and warning states (`Comp-States`): glyph tile, title, one
 * sentence, one action. Errors are announced (`alert`), the rest are regions.
 */
@Composable
fun StatePanel(
    title: String,
    line: String,
    modifier: Modifier = Modifier,
    tone: StateTone = StateTone.Neutral,
    actionText: String? = null,
    primaryAction: Boolean = false,
    onAction: (() -> Unit)? = null,
) {
    val c = Inst.colors
    val sp = InstUi.space
    val glyph = when (tone) {
        StateTone.Neutral -> stringResource(R.string.ui_state_glyph_empty)
        StateTone.Error -> stringResource(R.string.ui_state_glyph_error)
        StateTone.Warning -> stringResource(R.string.ui_state_glyph_warn)
    }
    val glyphColor = when (tone) { StateTone.Neutral -> c.text2; StateTone.Error -> c.error; StateTone.Warning -> c.warning }
    Column(
        modifier
            .fillMaxWidth()
            .background(c.surface)
            .padding(vertical = sp.s24, horizontal = sp.s16)
            .semantics(mergeDescendants = false) {
                if (tone == StateTone.Error) liveRegion = LiveRegionMode.Polite
            },
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(sp.s8, Alignment.CenterVertically),
    ) {
        Box(
            Modifier.size(dimensionResource(R.dimen.app_state_glyph)).background(c.surface2),
            contentAlignment = Alignment.Center,
        ) { InstText(glyph, InstUi.type.bodyStrong, color = glyphColor) }
        InstText(title, InstUi.type.bodyStrong, Modifier.semantics { heading() })
        InstText(
            line, InstUi.type.caption, Modifier.widthIn(max = dimensionResource(R.dimen.app_state_text_max)),
            color = c.text2,
        )
        if (actionText != null && onAction != null) {
            InstButton(
                actionText, onAction,
                kind = if (primaryAction) ButtonKind.Primary else ButtonKind.Secondary,
            )
        }
    }
}

/** A block the shape of the content that will replace it (first load); never a full-page spinner. */
@Composable
fun SkeletonBlock(modifier: Modifier = Modifier) {
    Box(modifier.background(Inst.colors.surface2))
}

/** Section label: uppercase, tracked (`t-label`). */
@Composable
fun SectionLabel(text: String, modifier: Modifier = Modifier, color: Color = Color.Unspecified) {
    InstText(text.uppercase(), InstUi.type.label, modifier.semantics { heading() }, color = color)
}

/** Segmented radio group (`i-seg`): equal-width options at 48dp, the selected one on surface. */
@Composable
fun <T> InstSegmented(
    options: List<Pair<T, String>>,
    selected: T,
    onSelect: (T) -> Unit,
    modifier: Modifier = Modifier,
) {
    val c = Inst.colors
    val sp = InstUi.space
    Row(
        modifier
            .fillMaxWidth()
            .background(c.surface2)
            .selectableGroup(),
    ) {
        for ((value, label) in options) {
            val on = value == selected
            Box(
                Modifier
                    .weight(1f)
                    .heightIn(min = sp.touchTarget)
                    .background(if (on) c.surface else Color.Transparent)
                    .selectable(selected = on, role = Role.RadioButton, onClick = { onSelect(value) }),
                contentAlignment = Alignment.Center,
            ) {
                InstText(label, if (on) InstUi.type.bodyStrong else InstUi.type.body, color = if (on) c.text else c.text2, maxLines = 1)
            }
        }
    }
}

/** The 56dp app bar on surface with the wordmark and the server's channel badge (`.ab`). */
@Composable
fun InstAppBar(
    title: String,
    modifier: Modifier = Modifier,
    channel: ServerChannel? = null,
    actions: @Composable () -> Unit = {},
) {
    val c = Inst.colors
    val sp = InstUi.space
    Column(modifier.fillMaxWidth().background(c.surface)) {
        Row(
            Modifier.fillMaxWidth().height(sp.appBar).padding(horizontal = sp.s16),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(sp.s8),
        ) {
            InstText(title, InstUi.type.wordmark, Modifier.semantics { heading() }, maxLines = 1)
            if (channel != null) ChannelBadge(channel)
            Box(Modifier.weight(1f))
            actions()
        }
        Box(Modifier.fillMaxWidth().height(sp.hairline).background(c.line))
    }
}

/** A thin vertical or horizontal rule in `line`. */
@Composable
fun Hairline(modifier: Modifier = Modifier) {
    Box(modifier.background(Inst.colors.line))
}

/** Border helper for outlined controls (`control` stroke, hairline). */
@Composable
fun Modifier.controlBorder(shape: Shape): Modifier =
    border(BorderStroke(InstUi.space.hairline, Inst.colors.control), shape)

/** Dims content to the "refreshing" state: old content stays visible at 50% while an update runs. */
fun Modifier.refreshing(active: Boolean): Modifier = if (active) alpha(0.5f) else this
