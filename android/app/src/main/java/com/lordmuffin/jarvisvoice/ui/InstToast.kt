package com.lordmuffin.jarvisvoice.ui

import android.app.Activity
import android.graphics.Typeface
import android.text.SpannableStringBuilder
import android.text.Spanned
import android.text.style.ForegroundColorSpan
import android.text.style.StyleSpan
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.FrameLayout
import android.widget.TextView
import android.widget.Toast
import androidx.annotation.StringRes
import com.lordmuffin.jarvisvoice.R

/**
 * Instrument's toast: an inverse-coloured strip near the bottom of the screen that goes away after 4 s.
 * System toasts can't be restyled (custom views are ignored from API 30), so activities draw this in
 * their own window. [lead] is the optional bold first word in `accent-on-inverse`. Services have no
 * window and keep using [Toast].
 */
object InstToast {
    private const val DURATION_MS = 4000L

    fun show(activity: Activity, @StringRes text: Int, lead: CharSequence? = null) =
        show(activity, activity.getString(text), lead)

    fun show(activity: Activity, text: CharSequence, lead: CharSequence? = null) {
        if (activity.isFinishing || activity.isDestroyed) {
            // Nothing to draw in; a result that lands after the screen is gone still reaches the user.
            Toast.makeText(activity.applicationContext, text, Toast.LENGTH_SHORT).show()
            return
        }
        val host = activity.findViewById<ViewGroup>(android.R.id.content)
        host.findViewWithTag<View>(TAG)?.let { host.removeView(it) }

        val density = activity.resources.displayMetrics.density
        val message = SpannableStringBuilder().apply {
            if (!lead.isNullOrEmpty()) {
                append(lead)
                setSpan(StyleSpan(Typeface.BOLD), 0, length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
                setSpan(ForegroundColorSpan(activity.getColor(R.color.inst_accent_on_inverse)), 0, length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
                append("  ")
            }
            append(text)
        }
        val view = TextView(activity).apply {
            tag = TAG
            this.text = message
            setTextColor(activity.getColor(R.color.inst_on_inverse))
            setTextSize(android.util.TypedValue.COMPLEX_UNIT_SP, 13f)
            setBackgroundResource(R.drawable.inst_toast_bg)
            val h = resources.getDimensionPixelSize(R.dimen.inst_space_16)
            val v = resources.getDimensionPixelSize(R.dimen.inst_space_12)
            setPadding(h, v, h, v)
            maxWidth = resources.getDimensionPixelSize(R.dimen.app_toast_max_width)
            elevation = 4 * density
            accessibilityLiveRegion = View.ACCESSIBILITY_LIVE_REGION_POLITE
        }
        // Sits above the bottom nav (56dp) on the tab screens, and just off the edge on the others.
        val lift = if (activity.findViewById<View>(R.id.bottom_nav_root) != null) R.dimen.inst_bottombar else R.dimen.inst_space_16
        val params = FrameLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
            gravity = Gravity.BOTTOM or Gravity.CENTER_HORIZONTAL
            bottomMargin = activity.resources.getDimensionPixelSize(lift) + activity.resources.getDimensionPixelSize(R.dimen.inst_space_16)
        }
        host.addView(view, params)
        view.postDelayed({ host.removeView(view) }, DURATION_MS)
    }

    private const val TAG = "inst_toast"
}
