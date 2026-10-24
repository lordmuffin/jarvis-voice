package com.lordmuffin.jarvisvoice.ui.compose

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.res.dimensionResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.input.KeyboardCapitalization
import com.lordmuffin.jarvisvoice.R
import instrument.Inst

/** Single-line text field: bg fill, control stroke, 12dp radius, 48dp tall (`i-input`). */
@Composable
fun InstTextField(
    value: String,
    onValueChange: (String) -> Unit,
    hint: String,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
) {
    val c = Inst.colors
    val sp = InstUi.space
    BasicTextField(
        value = value,
        onValueChange = onValueChange,
        enabled = enabled,
        singleLine = true,
        textStyle = InstUi.type.body,
        cursorBrush = SolidColor(c.accent),
        keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Sentences),
        modifier = modifier
            .fillMaxWidth()
            .heightIn(min = sp.touchTarget)
            .background(c.bg, InstUi.shapes.field)
            .border(sp.hairline, c.control, InstUi.shapes.field),
        decorationBox = { inner ->
            Box(Modifier.padding(horizontal = sp.s12), contentAlignment = Alignment.CenterStart) {
                if (value.isEmpty()) InstText(hint, InstUi.type.body, color = c.text2, maxLines = 1)
                inner()
            }
        },
    )
}

/**
 * Switch row (`i-switch`): 36x20 track, 14dp knob, accent when on, with the state spelled out as
 * ON / OFF so it never relies on colour. The whole row is the 48dp target.
 */
@Composable
fun InstSwitchRow(
    label: String,
    checked: Boolean,
    onCheckedChange: (Boolean) -> Unit,
    modifier: Modifier = Modifier,
) {
    val c = Inst.colors
    val sp = InstUi.space
    val track = dimensionResource(R.dimen.inst_switch_width)
    val trackH = dimensionResource(R.dimen.inst_switch_height)
    val knob = dimensionResource(R.dimen.inst_switch_knob)
    val inset = (trackH - knob) / 2
    Row(
        modifier
            .fillMaxWidth()
            .heightIn(min = sp.touchTarget)
            .toggleable(value = checked, role = Role.Switch, onValueChange = onCheckedChange),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(sp.s8),
    ) {
        Box(
            Modifier
                .width(track).height(trackH)
                .background(if (checked) c.accent else c.surface2, InstUi.shapes.round)
                .border(sp.hairline, if (checked) c.accent else c.control, InstUi.shapes.round),
        ) {
            Box(
                Modifier
                    .offset(x = if (checked) track - knob - inset else inset, y = inset)
                    .size(knob)
                    .background(if (checked) c.onAccent else c.control, InstUi.shapes.round),
            )
        }
        InstText(
            stringResource(if (checked) R.string.ui_switch_on else R.string.ui_switch_off),
            InstUi.type.metaMono, color = c.text2,
        )
        InstText(label, InstUi.type.body, Modifier.weight(1f))
    }
}

