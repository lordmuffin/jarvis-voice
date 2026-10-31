package com.lordmuffin.jarvisvoice.ui

import android.widget.Button
import androidx.annotation.ColorRes
import androidx.annotation.DrawableRes
import com.lordmuffin.jarvisvoice.R

/** The Instrument button roles (`Widget.Inst.Button.*`). Pick by importance, not by colour. */
enum class InstButtonKind(@param:DrawableRes val background: Int, @param:ColorRes val text: Int) {
    PRIMARY(R.drawable.inst_btn_primary, R.color.inst_btn_text_primary),
    SECONDARY(R.drawable.inst_btn_secondary, R.color.inst_btn_text_secondary),
    QUIET(R.drawable.inst_btn_quiet, R.color.inst_btn_text_quiet),
    DANGER(R.drawable.inst_btn_danger, R.color.inst_btn_text_danger),
}

/**
 * Re-roles a button at runtime, for the few that change meaning as state changes (Download → Cancel →
 * Delete on a model). Everything else sets its role once with `style="@style/Widget.Inst.Button.*"`.
 */
fun Button.setInstKind(kind: InstButtonKind) {
    setBackgroundResource(kind.background)
    setTextColor(context.getColorStateList(kind.text))
}
