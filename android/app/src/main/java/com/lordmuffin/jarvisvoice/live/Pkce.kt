package com.lordmuffin.jarvisvoice.live

import java.security.MessageDigest
import java.security.SecureRandom
import java.util.Base64

/**
 * PKCE (RFC 7636, S256) for the Pocket-ID sign-in handoff: the app keeps the verifier, sends the
 * challenge to `/auth/login`, and proves it holds the verifier when redeeming the one-time code.
 */
object Pkce {
    private val encoder = Base64.getUrlEncoder().withoutPadding()

    /** 32 random bytes, base64url without padding: 43 characters of `[A-Za-z0-9_-]`. */
    fun newVerifier(random: SecureRandom = SecureRandom()): String {
        val bytes = ByteArray(32).also(random::nextBytes)
        return encoder.encodeToString(bytes)
    }

    /** `base64url(SHA-256(verifier))` without padding: always 43 characters. */
    fun challenge(verifier: String): String {
        val digest = MessageDigest.getInstance("SHA-256").digest(verifier.toByteArray(Charsets.US_ASCII))
        return encoder.encodeToString(digest)
    }
}
