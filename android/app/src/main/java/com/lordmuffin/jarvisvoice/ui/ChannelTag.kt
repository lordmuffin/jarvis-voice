package com.lordmuffin.jarvisvoice.ui

import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.RectF
import android.graphics.Typeface
import android.text.SpannableStringBuilder
import android.text.Spanned
import android.text.style.ReplacementSpan
import android.widget.TextView
import androidx.annotation.ColorInt
import com.lordmuffin.jarvisvoice.R
import com.lordmuffin.jarvisvoice.live.ServerChannel
import kotlin.math.roundToInt

/** [text], then [channel]'s tag ([ChannelTagSpan]); just [text] while the channel is unknown. */
fun TextView.setTextWithChannel(text: CharSequence, channel: ServerChannel?) {
    if (channel == null) {
        this.text = text
        return
    }
    val out = SpannableStringBuilder(text)
    if (text.isNotEmpty()) out.append(' ')
    val start = out.length
    out.append(channel.tag)
    val span = ChannelTagSpan(context.getColor(channel.color), context.getColor(R.color.jv_bg), resources.displayMetrics.density)
    out.setSpan(span, start, out.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
    this.text = out
}

/**
 * The server's release channel as a small filled pill inside a line of text, like the dashboard's
 * wordmark tag and the Mac app's `ChannelTag`: bold, tracked, `jv_bg` on [fill]. Inline, so it can
 * sit beside the server's address wherever a screen names it, and wraps as one piece.
 */
class ChannelTagSpan(
    @param:ColorInt private val fill: Int,
    @param:ColorInt private val textColor: Int,
    private val density: Float,
) : ReplacementSpan() {
    private val rect = RectF()

    override fun getSize(paint: Paint, text: CharSequence, start: Int, end: Int, fm: Paint.FontMetricsInt?): Int =
        (tagPaint(paint).measureText(text, start, end) + 2 * PAD_H_DP * density).roundToInt()

    override fun draw(
        canvas: Canvas, text: CharSequence, start: Int, end: Int,
        x: Float, top: Int, y: Int, bottom: Int, paint: Paint,
    ) {
        val tag = tagPaint(paint)
        val line = paint.fontMetrics
        val own = tag.fontMetrics
        // Centred on the surrounding text rather than its baseline, since the tag is smaller.
        val centre = y + (line.ascent + line.descent) / 2
        val half = (own.descent - own.ascent) / 2 + PAD_V_DP * density
        rect.set(x, centre - half, x + getSize(paint, text, start, end, null), centre + half)
        tag.color = fill
        val radius = RADIUS_DP * density
        canvas.drawRoundRect(rect, radius, radius, tag)
        tag.color = textColor
        canvas.drawText(text, start, end, x + PAD_H_DP * density, centre - (own.ascent + own.descent) / 2, tag)
    }

    private fun tagPaint(base: Paint) = Paint(base).apply {
        typeface = Typeface.create(base.typeface, Typeface.BOLD)
        textSize = base.textSize * SCALE
        letterSpacing = TRACKING
    }

    private companion object {
        /** Relative to the line it sits in: 13sp text gets a ~10sp tag. */
        const val SCALE = 0.8f
        /** `design/README.md`, Type: uppercase gets tracking; `tag` is 0.1em. */
        const val TRACKING = 0.1f
        const val PAD_H_DP = 5f
        const val PAD_V_DP = 1.5f
        const val RADIUS_DP = 3f
    }
}
