package com.lordmuffin.jarvisvoice

import com.lordmuffin.jarvisvoice.LauncherRoute.Step
import org.junit.Assert.assertEquals
import org.junit.Test

/** What the launcher icon opens: onboarding once, then the missing grants, then Record. */
class LauncherRouteTest {

    private fun next(done: Boolean = true, overlay: Boolean = true, mic: Boolean = true, a11y: Boolean = true) =
        LauncherRoute.next(done, overlay, mic, a11y)

    @Test fun firstLaunchIsOnboarding() {
        assertEquals(Step.ONBOARDING, next(done = false))
        assertEquals(Step.ONBOARDING, next(done = false, overlay = false, mic = false, a11y = false))
    }

    /** The bug: with everything set up the icon used to open nothing. It opens Record. */
    @Test fun everythingGrantedOpensRecord() {
        assertEquals(Step.RECORD, next())
    }

    @Test fun missingGrantsAreAskedForInOrder() {
        assertEquals(Step.GRANT_OVERLAY, next(overlay = false, mic = false, a11y = false))
        assertEquals(Step.GRANT_MIC, next(mic = false, a11y = false))
        assertEquals(Step.GRANT_ACCESSIBILITY, next(a11y = false))
    }
}
