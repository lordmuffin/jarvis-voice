package com.lordmuffin.jarvisvoice.wear

import android.app.Activity
import android.content.Intent
import android.os.Bundle

/**
 * Entry point for the tile, the complication and the "Jarvis record" launcher entry (the one a watch
 * button is mapped to). It opens the record screen and asks it to start or stop; the screen does the
 * toggle once it is visible, so the user sees it and the microphone is granted (see [PendingToggle]).
 */
class ToggleActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        startActivity(
            Intent(this, WearMainActivity::class.java)
                .putExtra(WearMainActivity.EXTRA_TOGGLE, true)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP),
        )
        finish()
    }
}
