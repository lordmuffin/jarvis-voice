package com.lordmuffin.jarvisvoice

import android.app.Application
import androidx.work.WorkManager

class JarvisApp : Application() {

    companion object {
        const val TAG_DOWNLOAD = "jarvis_download"
    }

    override fun onCreate() {
        super.onCreate()
        DebugLog.init(this)

        // Cancel all WorkManager jobs on every cold start. This handles:
        //   - adb install -r  (am force-stop doesn't trigger UncaughtExceptionHandler,
        //     so RUNNING jobs stay in the DB across installs)
        //   - OOM kills / force-stop from Settings
        //   - Any non-graceful process death
        // Downloads are user-initiated — they just tap Download again if interrupted.
        runCatching { WorkManager.getInstance(this).cancelAllWork() }

        installCrashHandler()
    }

    private fun installCrashHandler() {
        val default = Thread.getDefaultUncaughtExceptionHandler()
        Thread.setDefaultUncaughtExceptionHandler { thread, ex ->
            try {
                DebugLog.e("CrashGuard", "Crash on $thread", ex)
                WorkManager.getInstance(this).cancelAllWork()
            } catch (_: Exception) { /* best effort */ }
            default?.uncaughtException(thread, ex)
        }
    }

    /** No-op — kept so callers in Service/Activity don't need to change. */
    fun markCleanLaunch() = Unit
}
