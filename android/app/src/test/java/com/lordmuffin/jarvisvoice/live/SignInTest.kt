package com.lordmuffin.jarvisvoice.live

import org.junit.Assert.assertEquals
import org.junit.Test

class SignInTest {

    @Test fun loginUrlCarriesClientAndChallenge() {
        assertEquals(
            "https://live.example.dev/auth/login?client=android&challenge=E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
            SignIn.loginUrl("https://live.example.dev/", "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"),
        )
    }

    @Test fun loginUrlKeepsAPathPrefix() {
        assertEquals(
            "http://10.0.0.5:8000/jarvis/auth/login?client=android&challenge=abc",
            SignIn.loginUrl(" http://10.0.0.5:8000/jarvis ", "abc"),
        )
    }

    @Test fun callbackPrefersErrorThenCode() {
        assertEquals(SignIn.Callback.Code("xyz"), SignIn.callback("xyz", null))
        assertEquals(SignIn.Callback.Error("access_denied"), SignIn.callback("xyz", "access_denied"))
        assertEquals(SignIn.Callback.Error("cancelled"), SignIn.callback(null, null))
        assertEquals(SignIn.Callback.Error("cancelled"), SignIn.callback("", ""))
    }

    @Test fun deviceLabelAvoidsRepeatingTheBrand() {
        assertEquals("Google Pixel 8", SignIn.deviceLabel("Google", "Pixel 8"))
        assertEquals("Samsung SM-S918B", SignIn.deviceLabel("samsung", "SM-S918B"))
        assertEquals("OnePlus 12", SignIn.deviceLabel("OnePlus", "OnePlus 12"))
        assertEquals("Pixel 8", SignIn.deviceLabel(null, "Pixel 8"))
        assertEquals("Android", SignIn.deviceLabel("", " "))
    }
}
