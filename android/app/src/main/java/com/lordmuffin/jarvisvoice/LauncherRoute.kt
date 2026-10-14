package com.lordmuffin.jarvisvoice

/** Where the launcher icon sends the user. `MainActivity` acts on the answer. */
object LauncherRoute {
    enum class Step { ONBOARDING, GRANT_OVERLAY, GRANT_MIC, GRANT_ACCESSIBILITY, RECORD }

    /** Record is home (the design's "Idle home (launcher)"); it is where everything ends up once set up. */
    fun next(onboardingDone: Boolean, overlayOk: Boolean, micOk: Boolean, accessibilityOk: Boolean): Step = when {
        !onboardingDone -> Step.ONBOARDING
        !overlayOk -> Step.GRANT_OVERLAY
        !micOk -> Step.GRANT_MIC
        !accessibilityOk -> Step.GRANT_ACCESSIBILITY
        else -> Step.RECORD
    }
}
