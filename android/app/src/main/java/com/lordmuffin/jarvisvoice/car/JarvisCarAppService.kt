package com.lordmuffin.jarvisvoice.car

import android.content.Intent
import android.os.Bundle
import android.os.ResultReceiver
import androidx.car.app.AppEnvironment
import androidx.car.app.CarAppService
import androidx.car.app.Screen
import androidx.car.app.Session
import androidx.car.app.SessionManager
import androidx.car.app.validation.HostValidator
import com.lordmuffin.jarvisvoice.JarvisApp

/**
 * Android Auto CarAppService – the head-unit entry point.
 * Registers the car session that drives the conversation UI.
 */
class JarvisCarAppService : CarAppService() {

    private val sessionManager by lazy { getSystemService(SessionManager::class.java) }

    override fun onCreateHostValidator(): HostValidator {
        // Accept all hosts for development; in production, restrict to specific car brands
        return HostValidator.ALLOW_ALL_HOSTS_VALIDATOR
    }

    override fun onCreateScreen(intent: Intent): Screen {
        return JarvisCarScreen(this)
    }

    override fun onCreateSession(intent: Intent): Session {
        return JarvisCarSession(this)
    }

    /**
     * CarSession for managing the car app lifecycle and media controls.
     */
    private inner class JarvisCarSession(context: androidx.car.app.CarContext) : Session(context) {
        private var isInitialized = false

        override fun onInitialize(intent: Intent) {
            super.onInitialize(intent)
            if (isInitialized) return
            isInitialized = true

            // Initialize the media session controller
            JarvisMediaSessionController.getInstance(context)
        }

        override fun onGetScreen(intent: Intent): Screen {
            return JarvisCarScreen(context as androidx.car.app.CarContext)
        }

        override fun onDestroy() {
            super.onDestroy()
            // Cleanup media session
            JarvisMediaSessionController.getInstance(context)?.destroy()
        }
    }
}
