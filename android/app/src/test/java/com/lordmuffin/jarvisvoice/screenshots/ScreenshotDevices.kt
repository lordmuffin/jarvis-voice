package com.lordmuffin.jarvisvoice.screenshots

import app.cash.paparazzi.DeviceConfig
import app.cash.paparazzi.Paparazzi
import com.android.resources.Density
import com.android.resources.NightMode
import com.android.resources.ScreenOrientation
import com.android.resources.ScreenRatio
import com.android.resources.ScreenSize

/**
 * The displays every screenshot is taken on. Pixel Fold-class geometry (the Pixel 11 Pro Fold is
 * assumed to match the 9 Pro Fold): the cover display folded, the inner display unfolded. The inner
 * display is 851dp wide, past the `w840dp` breakpoint the two-pane layouts switch on.
 */
enum class Display(val label: String, private val base: DeviceConfig) {
    COVER("cover", DeviceConfig.PIXEL_6.copy(
        screenWidth = 1080, screenHeight = 2424, xdpi = 422, ydpi = 422,
        density = Density.create(420), size = ScreenSize.NORMAL, ratio = ScreenRatio.LONG,
        orientation = ScreenOrientation.PORTRAIT,
    )),
    INNER("inner", DeviceConfig.PIXEL_6.copy(
        screenWidth = 2076, screenHeight = 2152, xdpi = 373, ydpi = 373,
        density = Density.create(390), size = ScreenSize.XLARGE, ratio = ScreenRatio.NOTLONG,
        orientation = ScreenOrientation.PORTRAIT,
    ));

    fun config(dark: Boolean): DeviceConfig = base.copy(nightMode = if (dark) NightMode.NIGHT else NightMode.NOTNIGHT)
}

/** One cell of the display × appearance matrix. */
data class Variant(val display: Display, val dark: Boolean) {
    val tag: String get() = "${display.label}_${if (dark) "dark" else "light"}"
    override fun toString() = tag

    companion object {
        val ALL: List<Variant> = Display.values().flatMap { d -> listOf(false, true).map { Variant(d, it) } }
    }
}

fun paparazziFor(variant: Variant, theme: String = "Theme.Jarvis") = Paparazzi(
    deviceConfig = variant.display.config(variant.dark),
    theme = theme,
    showSystemUi = false,
)
