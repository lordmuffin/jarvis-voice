package com.lordmuffin.jarvisvoice.wear.sync

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import com.lordmuffin.jarvisvoice.wearcommon.sync.SyncEnvironment

class WearEnvironment(private val context: Context, private val config: WatchConfig) : SyncEnvironment {
    override fun serverConfigured() = config.isConfigured

    /** Wi-Fi, LTE, or the phone's connection proxied over Bluetooth: anything the system says reaches the internet. */
    override fun networkAvailable(): Boolean {
        val cm = context.getSystemService(ConnectivityManager::class.java) ?: return false
        val caps = cm.getNetworkCapabilities(cm.activeNetwork ?: return false) ?: return false
        return caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET) &&
            caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED)
    }
}
