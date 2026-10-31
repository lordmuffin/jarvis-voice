package com.lordmuffin.jarvisvoice.pill

import android.content.Context
import android.graphics.drawable.GradientDrawable
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.util.AttributeSet
import android.view.View
import android.view.animation.AnimationUtils
import android.widget.FrameLayout
import android.widget.ProgressBar
import android.widget.TextView
import androidx.annotation.ColorRes
import androidx.annotation.StringRes
import com.lordmuffin.jarvisvoice.R
import com.lordmuffin.jarvisvoice.ui.AudioWaveformView

/**
 * The dictation pill (`Jarvis-And-Pill`): 48dp, 24dp radius, a 1.5dp border whose colour carries the
 * state, `inst_surface_2` fill. [render] shows the children one [PillState] needs; nothing else in the
 * app draws the pill. Reads `inst_*` colours, so it follows the system day/night when the service
 * inflates it through a `Theme.Jarvis` context.
 */
class PillView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyle: Int = 0,
) : FrameLayout(context, attrs, defStyle) {

    private val dot: View by lazy { findViewById(R.id.pill_dot) }
    private val glyph: TextView by lazy { findViewById(R.id.pill_glyph) }
    private val spinner: ProgressBar by lazy { findViewById(R.id.pill_spinner) }
    private val label: TextView by lazy { findViewById(R.id.pill_label) }
    private val meter: AudioWaveformView by lazy { findViewById<AudioWaveformView>(R.id.waveform).also { it.barWidthDp = 3f; it.barGapDp = 2f } }
    private val time: TextView by lazy { findViewById(R.id.pill_time) }
    private val divider: View by lazy { findViewById(R.id.pill_divider) }
    private val action: TextView by lazy { findViewById(R.id.pill_action) }

    private val ticker = Handler(Looper.getMainLooper())
    private var listeningSince = -1L
    private var shown: PillState? = null

    private val tick = object : Runnable {
        override fun run() {
            if (listeningSince < 0) return
            val text = PillFormat.elapsed(SystemClock.elapsedRealtime() - listeningSince)
            time.text = text
            contentDescription = context.getString(R.string.pill_cd_listening, text)
            ticker.postDelayed(this, 1_000L)
        }
    }

    override fun onFinishInflate() {
        super.onFinishInflate()
        // The service inflates without a parent, so the layout's height is dropped and the pill wraps
        // its content. Hold every state at half again the listening row's height so it is easy to tap.
        val unspecified = MeasureSpec.makeMeasureSpec(0, MeasureSpec.UNSPECIFIED)
        val row = listOf(label, time).maxOf { it.measure(unspecified, unspecified); it.measuredHeight }
        minimumHeight = (row * HEIGHT_SCALE).toInt()
    }

    fun render(state: PillState) {
        if (state == shown) return
        shown = state
        ticker.removeCallbacks(tick)
        listeningSince = -1L
        meter.stopAnimation()
        dot.clearAnimation()
        for (v in listOf(dot, glyph, spinner, label, meter, time, divider, action)) v.visibility = GONE

        when (state) {
            PillState.Idle -> {
                border(R.color.inst_control)
                breathing(R.color.inst_accent)
                contentDescription = context.getString(R.string.pill_cd_idle)
            }
            is PillState.Listening -> {
                border(R.color.inst_accent)
                breathing(R.color.inst_accent)
                show(label, R.string.pill_listening, R.color.inst_accent)
                meter.barColor = context.getColor(R.color.inst_accent)
                meter.visibility = VISIBLE
                meter.startAnimation()
                time.setTextColor(context.getColor(R.color.inst_text))
                time.visibility = VISIBLE
                listeningSince = state.startedAtMs
                tick.run()
            }
            PillState.Processing -> {
                border(R.color.inst_control)
                spinner.visibility = VISIBLE
                show(label, R.string.pill_processing, R.color.inst_text_2)
                contentDescription = context.getString(R.string.pill_cd_processing)
            }
            is PillState.Done -> {
                border(R.color.inst_accent)
                glyph.visibility = VISIBLE
                contentDescription = context.getString(R.string.pill_cd_done)
            }
            is PillState.Error -> renderError(state.kind)
        }
    }

    private fun renderError(kind: PillError) {
        when (kind) {
            PillError.NO_SPEECH -> {
                border(R.color.inst_error)
                show(label, R.string.pill_err_no_speech, R.color.inst_error)
                contentDescription = context.getString(R.string.pill_cd_no_speech)
            }
            PillError.MIC_OFF -> {
                border(R.color.inst_error)
                show(label, R.string.pill_err_mic_off, R.color.inst_error)
                showAction(R.string.pill_action_open_settings)
                contentDescription = context.getString(R.string.pill_cd_mic_off)
            }
            PillError.MIC_BUSY -> {
                border(R.color.inst_control, dashed = true)
                show(label, R.string.pill_err_mic_busy, R.color.inst_text_2)
                contentDescription = context.getString(R.string.pill_cd_mic_busy)
            }
            PillError.ENGINE_UNAVAILABLE -> {
                border(R.color.inst_error)
                show(label, R.string.pill_err_engine, R.color.inst_error)
                showAction(R.string.pill_action_open_models)
                contentDescription = context.getString(R.string.pill_cd_engine)
            }
        }
    }

    private fun show(view: TextView, @StringRes text: Int, @ColorRes color: Int) {
        view.setText(text)
        view.setTextColor(context.getColor(color))
        view.visibility = VISIBLE
    }

    private fun showAction(@StringRes text: Int) {
        divider.visibility = VISIBLE
        action.setText(text)
        action.visibility = VISIBLE
    }

    private fun breathing(@ColorRes color: Int) {
        dot.backgroundTintList = android.content.res.ColorStateList.valueOf(context.getColor(color))
        dot.visibility = VISIBLE
        // Reduced motion holds the dot still.
        val animate = android.provider.Settings.Global.getFloat(
            context.contentResolver, android.provider.Settings.Global.ANIMATOR_DURATION_SCALE, 1f) != 0f
        if (animate) dot.startAnimation(AnimationUtils.loadAnimation(context, R.anim.breathe))
    }

    private fun border(@ColorRes color: Int, dashed: Boolean = false) {
        val res = resources
        background = GradientDrawable().apply {
            setColor(context.getColor(R.color.inst_surface_2))
            cornerRadius = res.getDimension(R.dimen.inst_radius_pill)
            val width = res.getDimensionPixelSize(R.dimen.inst_border_active)
            if (dashed) setStroke(width, context.getColor(color), res.displayMetrics.density * 3, res.displayMetrics.density * 2)
            else setStroke(width, context.getColor(color))
        }
    }

    override fun onDetachedFromWindow() {
        ticker.removeCallbacks(tick)
        meter.stopAnimation()
        super.onDetachedFromWindow()
    }

    override fun performClick(): Boolean = super.performClick()

    private companion object {
        const val HEIGHT_SCALE = 1.5f
    }
}
