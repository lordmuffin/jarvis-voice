package com.lordmuffin.jarvisvoice.live

import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.lordmuffin.jarvisvoice.R
import com.lordmuffin.jarvisvoice.SettingsActivity
import com.lordmuffin.jarvisvoice.ui.InstToast
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch

/**
 * Receives `com.lordmuffin.jarvisvoice://auth/callback?code=…` (or `?error=…`, with `account=…`
 * naming the refused account on `access_denied`) after the Pocket-ID
 * sign-in that Settings opened in the browser, redeems the one-time code with the stored PKCE
 * verifier for a device token, then returns to Settings.
 */
class AuthCallbackActivity : AppCompatActivity() {

    private lateinit var tvMessage: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_auth_callback)
        tvMessage = findViewById(R.id.tv_auth_message)
        // The code is single-use: only the first delivery of this intent may redeem it. If the
        // activity was recreated mid-redeem, Settings shows the outcome via /v1/me.
        if (savedInstanceState == null) handle(intent) else finishWith(null)
    }

    private fun handle(intent: Intent) {
        val data = intent.data
        val result = SignIn.callback(data?.getQueryParameter("code"), data?.getQueryParameter("error"))
        val settings = LiveSettings(this)
        val pending = settings.pendingSignIn()

        when (result) {
            is SignIn.Callback.Error -> {
                settings.clearPendingSignIn()
                finishWith(
                    when (result.error) {
                        "access_denied" -> data?.getQueryParameter("account")?.takeIf { it.isNotBlank() }
                            ?.let { getString(R.string.live_signin_denied_as, it) }
                            ?: getString(R.string.live_signin_denied)
                        "cancelled" -> getString(R.string.live_signin_cancelled)
                        "server_error" -> getString(R.string.live_signin_server_error)
                        else -> getString(R.string.live_signin_failed, result.error)
                    }
                )
            }
            is SignIn.Callback.Code -> {
                if (pending == null) {
                    finishWith(getString(R.string.live_signin_no_pending))
                    return
                }
                tvMessage.text = getString(R.string.live_signin_working)
                redeem(settings, pending, result.code)
            }
        }
    }

    private fun redeem(settings: LiveSettings, pending: LiveSettings.PendingSignIn, code: String) {
        lifecycleScope.launch {
            val message = try {
                val deviceName = SignIn.deviceLabel(Build.MANUFACTURER, Build.MODEL)
                val token = LiveApiClient(pending.serverUrl, "").redeemDeviceCode(code, pending.verifier, deviceName)
                settings.serverUrl = pending.serverUrl
                settings.deviceToken = token.token
                settings.accountName = null
                val name = try {
                    settings.apiClient().me().let { me -> me.displayName.ifBlank { me.email.orEmpty() } }
                } catch (e: CancellationException) {
                    throw e
                } catch (e: Exception) {
                    null  // signed in; Settings retries /v1/me on resume
                }
                settings.accountName = name
                if (name.isNullOrBlank()) getString(R.string.live_signin_success_unknown)
                else getString(R.string.live_signin_success, name)
            } catch (e: CancellationException) {
                throw e
            } catch (e: LiveHttpException) {
                if (e.status == 400) getString(R.string.live_signin_code_rejected)
                else getString(R.string.live_signin_failed, getString(R.string.livesess_http, e.status))
            } catch (e: Exception) {
                getString(R.string.live_signin_failed, e.message ?: e.javaClass.simpleName)
            } finally {
                // One attempt per sign-in: the server burns the code whatever the outcome.
                settings.clearPendingSignIn()
            }
            finishWith(message)
        }
    }

    /** Toasts [message] and returns to Settings, closing the browser tab that sat above it. */
    private fun finishWith(message: String?) {
        if (message != null) InstToast.show(this, message)
        startActivity(
            Intent(this, SettingsActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP)
        )
        finish()
    }
}
