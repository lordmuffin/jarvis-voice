package com.lordmuffin.jarvisvoice.wear

import android.content.Context
import android.os.Build
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager

/** One firm pulse on start, two short ones on stop, so the wrist tells them apart with the screen off. */
object Haptics {
    fun started(ctx: Context) = play(ctx, longArrayOf(0, 60))
    fun stopped(ctx: Context) = play(ctx, longArrayOf(0, 40, 80, 40))

    private fun play(ctx: Context, pattern: LongArray) {
        val v = vibrator(ctx) ?: return
        if (!v.hasVibrator()) return
        v.vibrate(VibrationEffect.createWaveform(pattern, -1))
    }

    private fun vibrator(ctx: Context): Vibrator? =
        if (Build.VERSION.SDK_INT >= 31) ctx.getSystemService(VibratorManager::class.java)?.defaultVibrator
        else @Suppress("DEPRECATION") ctx.getSystemService(Vibrator::class.java)
}
