package com.lordmuffin.jarvisvoice

import android.app.Application
import androidx.work.WorkManager

class JarvisApp : Application() {

    companion object {
        private const val PREFS      = "jarvis_crash_guard"
        private const val KEY_CLEAN  = "launched_cleanly"
        const val TAG_DOWNLOAD       = "jarvis_download"
    }

    override fun onCreate() {
        super.onCreate()

        val prefs = getSharedPreferences(PREFS, MODE_PRIVATE)

        if (!prefs.getBoolean(KEY_CLEAN, true)) {
            // Previous launch did not complete cleanly (crash or force-kill before
            // markCleanLaunch() was called). Cancel all download jobs so a stuck
            // WorkManager job can't restart and crash the process before the UI renders.
            WorkManager.getInstance(this).cancelAllWorkByTag(TAG_DOWNLOAD)
            DebugLog.i("CrashGuard", "Prior crash detected — cancelled download jobs")
        }
        // Assume not-clean until MainActivity confirms a successful start.
        prefs.edit().putBoolean(KEY_CLEAN, false).apply()

        installCrashHandler(prefs.getBoolean(KEY_CLEAN, false))
    }

    private fun installCrashHandler(wasClean: Boolean) {
        val default = Thread.getDefaultUncaughtExceptionHandler()
        Thread.setDefaultUncaughtExceptionHandler { thread, ex ->
            try {
                // .commit() is synchronous — must complete before the process is killed.
                getSharedPreferences(PREFS, MODE_PRIVATE)
                    .edit().putBoolean(KEY_CLEAN, false).commit()
                WorkManager.getInstance(this).cancelAllWorkByTag(TAG_DOWNLOAD)
                DebugLog.e("CrashGuard", "Crash on $thread — download jobs cancelled", ex)
            } catch (_: Exception) { /* best effort */ }
            default?.uncaughtException(thread, ex)
        }
    }

    /** Call from MainActivity.onResume() once the app is confirmed functional. */
    fun markCleanLaunch() {
        getSharedPreferences(PREFS, MODE_PRIVATE)
            .edit().putBoolean(KEY_CLEAN, true).apply()
    }
}
