package com.lordmuffin.jarvisvoice.live

import android.content.Context
import android.net.ConnectivityManager
import android.net.LinkProperties
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.coroutines.flow.conflate

/** Works out the phone's [NetworkPlace], and says when its networks change. */
class NetworkPlaceMonitor(context: Context) {
    private val cm = context.getSystemService(ConnectivityManager::class.java)

    /**
     * Emits on collection and on every network change: Wi-Fi joined or lost, a VPN up or down,
     * addresses changed. Bursty; debounce before checking.
     */
    fun changes(): Flow<Unit> = callbackFlow {
        val callback = object : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(network: Network) { trySend(Unit) }
            override fun onLost(network: Network) { trySend(Unit) }
            override fun onLinkPropertiesChanged(network: Network, linkProperties: LinkProperties) { trySend(Unit) }
        }
        // VPNs included: a plain request leaves them out, and NetBird is one.
        val request = NetworkRequest.Builder()
            .removeCapability(NetworkCapabilities.NET_CAPABILITY_NOT_VPN)
            .build()
        trySend(Unit)
        cm.registerNetworkCallback(request, callback)
        awaitClose { cm.unregisterNetworkCallback(callback) }
    }.conflate()

    /** NetBird first (no network round trip), then the server's health check. */
    suspend fun check(api: LiveApiClient): NetworkPlace = when {
        NetBird.detect(interfaces()) -> NetworkPlace.NETBIRD
        api.isReachable() -> NetworkPlace.HOME
        else -> NetworkPlace.AWAY
    }

    @Suppress("DEPRECATION")  // allNetworks: its replacement is callback-only.
    private fun interfaces(): List<NetInterface> = cm.allNetworks.mapNotNull { network ->
        val link = cm.getLinkProperties(network) ?: return@mapNotNull null
        val vpn = cm.getNetworkCapabilities(network)?.hasTransport(NetworkCapabilities.TRANSPORT_VPN) == true
        NetInterface(link.interfaceName, vpn, link.linkAddresses.mapNotNull { it.address.hostAddress })
    }
}
