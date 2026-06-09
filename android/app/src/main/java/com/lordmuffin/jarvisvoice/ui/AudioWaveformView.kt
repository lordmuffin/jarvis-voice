package com.lordmuffin.jarvisvoice.ui

import android.animation.ValueAnimator
import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.util.AttributeSet
import android.view.View
import android.view.animation.LinearInterpolator

class AudioWaveformView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyle: Int = 0
) : View(context, attrs, defStyle) {

    var barColor: Int = Color.parseColor("#00B4D8")
        set(value) { field = value; paint.color = value; invalidate() }

    private val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.parseColor("#00B4D8")
        style = Paint.Style.FILL
    }

    private val barCount = 3
    private val barRects = Array(barCount) { RectF() }
    private val animators = Array(barCount) { ValueAnimator() }
    private val amplitudes = FloatArray(barCount) { 0.3f }

    private val barWidthDp = 4f
    private val barGapDp = 6f
    private val barCornerDp = 2f

    fun startAnimation() {
        for (i in 0 until barCount) {
            animators[i].cancel()
            animators[i] = ValueAnimator.ofFloat(0.2f, 1.0f, 0.2f).apply {
                duration = 600L + i * 120L
                repeatCount = ValueAnimator.INFINITE
                repeatMode = ValueAnimator.RESTART
                startDelay = i * 80L
                interpolator = LinearInterpolator()
                addUpdateListener { anim ->
                    amplitudes[i] = anim.animatedValue as Float
                    invalidate()
                }
            }
            animators[i].start()
        }
    }

    fun stopAnimation() {
        for (animator in animators) animator.cancel()
        for (i in amplitudes.indices) amplitudes[i] = 0.3f
        invalidate()
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        val density = resources.displayMetrics.density
        val barW = barWidthDp * density
        val gap = barGapDp * density
        val corner = barCornerDp * density
        val totalW = barCount * barW + (barCount - 1) * gap
        val startX = (width - totalW) / 2f
        val centerY = height / 2f

        for (i in 0 until barCount) {
            val barH = height * amplitudes[i] * 0.8f
            val left = startX + i * (barW + gap)
            val right = left + barW
            val top = centerY - barH / 2f
            val bottom = centerY + barH / 2f
            barRects[i].set(left, top, right, bottom)
            canvas.drawRoundRect(barRects[i], corner, corner, paint)
        }
    }
}
