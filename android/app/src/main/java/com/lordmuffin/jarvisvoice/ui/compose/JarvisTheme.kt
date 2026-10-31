package com.lordmuffin.jarvisvoice.ui.compose

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.shape.CornerSize
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.ReadOnlyComposable
import androidx.compose.runtime.remember
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.res.dimensionResource
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.TextUnit
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.em
import com.lordmuffin.jarvisvoice.R
import instrument.Inst
import instrument.InstTheme

/**
 * The Instrument design system in Compose. Colours come from the vendored `instrument.InstTheme`;
 * spacing, sizes, radii and type sizes are read from the vendored `@dimen/inst_*` resources, so the
 * Compose and View screens share one set of numbers. Nothing here (or in any screen built on it)
 * carries a literal colour, dp or sp.
 */
@Composable
fun JarvisTheme(darkTheme: Boolean = isSystemInDarkTheme(), content: @Composable () -> Unit) {
    InstTheme(darkTheme) {
        val space = instSpace()
        val shapes = instShapes()
        val type = instTypography(Inst.colors.text, Inst.colors.text2)
        CompositionLocalProvider(
            LocalInstSpace provides space,
            LocalInstShapes provides shapes,
            LocalInstType provides type,
            content = content,
        )
    }
}

/** Access point next to [Inst.colors]: `InstUi.space`, `InstUi.shapes`, `InstUi.type`. */
object InstUi {
    val space: InstSpace @Composable @ReadOnlyComposable get() = LocalInstSpace.current
    val shapes: InstShapes @Composable @ReadOnlyComposable get() = LocalInstShapes.current
    val type: InstTypography @Composable @ReadOnlyComposable get() = LocalInstType.current
}

@Immutable
class InstSpace(
    val s4: Dp, val s8: Dp, val s12: Dp, val s16: Dp, val s24: Dp, val s32: Dp, val s48: Dp,
    val controlSm: Dp, val controlMd: Dp, val controlLg: Dp,
    val row: Dp, val header: Dp, val bottomBar: Dp,
    val touchTarget: Dp, val contentMax: Dp,
    val dot: Dp, val dotLive: Dp,
    val hairline: Dp, val borderActive: Dp, val borderFocus: Dp,
    val appBar: Dp, val railWidth: Dp, val railItem: Dp, val railEdge: Dp,
    val sessionPane: Dp, val listPane: Dp,
)

@Immutable
class InstShapes(
    val square: RoundedCornerShape,
    val field: RoundedCornerShape,
    val bubble: RoundedCornerShape,
    val pill: RoundedCornerShape,
    val round: RoundedCornerShape,
)

@Immutable
class InstTypography(
    val display: TextStyle, val title: TextStyle, val stat: TextStyle, val bodyLg: TextStyle,
    val chat: TextStyle, val body: TextStyle, val bodyStrong: TextStyle, val caption: TextStyle,
    val meta: TextStyle, val metaMono: TextStyle, val label: TextStyle, val micro: TextStyle,
    val tag: TextStyle, val wordmark: TextStyle, val button: TextStyle, val navIcon: TextStyle,
)

internal val LocalInstSpace = staticCompositionLocalOf<InstSpace> { error("JarvisTheme missing") }
internal val LocalInstShapes = staticCompositionLocalOf<InstShapes> { error("JarvisTheme missing") }
internal val LocalInstType = staticCompositionLocalOf<InstTypography> { error("JarvisTheme missing") }

@Composable
private fun d(id: Int): Dp = dimensionResource(id)

@Composable
private fun instSpace() = InstSpace(
    s4 = d(R.dimen.inst_space_4), s8 = d(R.dimen.inst_space_8), s12 = d(R.dimen.inst_space_12),
    s16 = d(R.dimen.inst_space_16), s24 = d(R.dimen.inst_space_24), s32 = d(R.dimen.inst_space_32),
    s48 = d(R.dimen.inst_space_48),
    controlSm = d(R.dimen.inst_control_sm), controlMd = d(R.dimen.inst_control_md), controlLg = d(R.dimen.inst_control_lg),
    row = d(R.dimen.inst_row), header = d(R.dimen.inst_header), bottomBar = d(R.dimen.inst_bottombar),
    touchTarget = d(R.dimen.inst_touch_target_android), contentMax = d(R.dimen.inst_content_max),
    dot = d(R.dimen.inst_dot), dotLive = d(R.dimen.inst_dot_live),
    hairline = d(R.dimen.inst_border_hairline), borderActive = d(R.dimen.inst_border_active),
    borderFocus = d(R.dimen.inst_border_focus),
    appBar = d(R.dimen.app_appbar), railWidth = d(R.dimen.app_rail_width),
    railItem = d(R.dimen.app_rail_item), railEdge = d(R.dimen.app_rail_edge),
    sessionPane = d(R.dimen.app_session_pane), listPane = d(R.dimen.app_list_pane),
)

@Composable
private fun instShapes(): InstShapes {
    val field = d(R.dimen.inst_radius_field)
    val bubble = d(R.dimen.inst_radius_bubble)
    val pill = d(R.dimen.inst_radius_pill)
    val zero = d(R.dimen.inst_radius_0)
    return remember(field, bubble, pill, zero) {
        InstShapes(
            square = RoundedCornerShape(zero), field = RoundedCornerShape(field),
            bubble = RoundedCornerShape(bubble), pill = RoundedCornerShape(pill),
            round = RoundedCornerShape(CornerSize(50)),
        )
    }
}

/** A `@dimen/inst_type_*` size as sp. Dimension resources fold the user's font scale in already. */
@Composable
private fun sp(id: Int): TextUnit = with(LocalDensity.current) { dimensionResource(id).toSp() }

@Composable
private fun instTypography(text: androidx.compose.ui.graphics.Color, text2: androidx.compose.ui.graphics.Color): InstTypography {
    val bold = FontWeight.Bold
    fun base(size: TextUnit) = TextStyle(fontFamily = FontFamily.Default, fontSize = size, color = text)
    val caption = base(sp(R.dimen.inst_type_caption)).copy(color = text2)
    val meta = base(sp(R.dimen.inst_type_meta)).copy(color = text2)
    return InstTypography(
        display = base(sp(R.dimen.inst_type_display)).copy(fontWeight = bold),
        title = base(sp(R.dimen.inst_type_title)).copy(fontWeight = bold),
        stat = base(sp(R.dimen.inst_type_stat)).copy(fontWeight = bold, fontFamily = FontFamily.Monospace),
        bodyLg = base(sp(R.dimen.inst_type_body_lg)),
        chat = base(sp(R.dimen.inst_type_chat)),
        body = base(sp(R.dimen.inst_type_body)),
        bodyStrong = base(sp(R.dimen.inst_type_body)).copy(fontWeight = bold),
        caption = caption,
        meta = meta,
        metaMono = meta.copy(fontFamily = FontFamily.Monospace),
        // Section label: uppercase is applied by the caller (Text is not transformed here), tracked 0.08em.
        label = meta.copy(letterSpacing = 0.08.em),
        micro = base(sp(R.dimen.inst_type_micro)).copy(color = text2),
        // Tag: bold, tracked 0.1em (chips, badges, speaker tags).
        tag = base(sp(R.dimen.inst_type_tag)).copy(color = text2, fontWeight = bold, letterSpacing = 0.1.em),
        wordmark = base(sp(R.dimen.inst_type_wordmark)).copy(fontWeight = bold),
        button = base(sp(R.dimen.inst_type_body)).copy(fontWeight = bold),
        navIcon = base(sp(R.dimen.app_nav_icon)),
    )
}
