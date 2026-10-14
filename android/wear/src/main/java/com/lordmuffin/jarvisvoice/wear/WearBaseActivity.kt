package com.lordmuffin.jarvisvoice.wear

import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import androidx.appcompat.app.AppCompatDelegate

/** Instrument's dark palette (values-night) is the watch palette; pin it regardless of system mode. */
abstract class WearBaseActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        AppCompatDelegate.setDefaultNightMode(AppCompatDelegate.MODE_NIGHT_YES)
        super.onCreate(savedInstanceState)
    }
}
