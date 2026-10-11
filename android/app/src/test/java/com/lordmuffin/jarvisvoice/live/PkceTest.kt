package com.lordmuffin.jarvisvoice.live

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.security.SecureRandom

class PkceTest {

    private val base64url = Regex("^[A-Za-z0-9_-]+$")

    @Test fun challengeMatchesRfc7636Vector() {
        // RFC 7636 appendix B (verifier from the 32 octets 116, 24, 223, 180, …).
        assertEquals(
            "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
            Pkce.challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"),
        )
    }

    @Test fun verifierEncodesRandomOctetsAsRfc7636Does() {
        val octets = intArrayOf(
            116, 24, 223, 180, 151, 153, 224, 37, 79, 250, 96, 125, 216, 173, 187, 186,
            22, 212, 37, 77, 105, 214, 191, 240, 91, 88, 5, 88, 83, 132, 141, 121,
        ).map { it.toByte() }.toByteArray()
        val fixed = object : SecureRandom() {
            override fun nextBytes(bytes: ByteArray) {
                octets.copyInto(bytes)
            }
        }
        assertEquals("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk", Pkce.newVerifier(fixed))
    }

    @Test fun verifierIs43UnpaddedBase64UrlChars() {
        repeat(50) {
            val v = Pkce.newVerifier()
            assertEquals(43, v.length)
            assertTrue(v, base64url.matches(v))
        }
    }

    @Test fun challengeIs43CharsAndMatchesServerPattern() {
        repeat(50) {
            val c = Pkce.challenge(Pkce.newVerifier())
            assertEquals(43, c.length)
            assertTrue(c, base64url.matches(c))
        }
    }

    @Test fun verifiersAreRandom() {
        assertNotEquals(Pkce.newVerifier(), Pkce.newVerifier())
    }
}
