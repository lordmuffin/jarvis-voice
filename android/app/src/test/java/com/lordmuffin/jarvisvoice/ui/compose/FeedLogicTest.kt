package com.lordmuffin.jarvisvoice.ui.compose

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class FollowLiveTest {
    @Test fun startsFollowing() = assertTrue(FollowLive().following)

    @Test fun userScrollingAwayFromTheEndPausesFollowing() {
        val f = FollowLive()
        f.onScroll(atEnd = false, userDragging = true)
        assertFalse(f.following)
    }

    @Test fun newLinesGrowingTheListDoNotPauseFollowing() {
        // The end moves away from the viewport when a line is appended; that is not the user leaving.
        val f = FollowLive()
        f.onScroll(atEnd = false, userDragging = false)
        assertTrue(f.following)
    }

    @Test fun scrollingBackToTheEndResumes() {
        val f = FollowLive()
        f.onScroll(atEnd = false, userDragging = true)
        f.onScroll(atEnd = true, userDragging = true)
        assertTrue(f.following)
    }

    @Test fun jumpToLiveResumes() {
        val f = FollowLive()
        f.onScroll(atEnd = false, userDragging = true)
        f.jumpToLive()
        assertTrue(f.following)
    }

    @Test fun pausedStaysPausedWhileLinesArrive() {
        val f = FollowLive()
        f.onScroll(atEnd = false, userDragging = true)
        f.onScroll(atEnd = false, userDragging = false)
        assertFalse(f.following)
    }
}

class LevelHistoryTest {
    @Test fun keepsOnlyTheNewestSamples() {
        val h = LevelHistory(capacity = 3)
        listOf(0.1f, 0.2f, 0.3f, 0.4f).forEach(h::push)
        assertEquals(listOf(0.2f, 0.3f, 0.4f), h.snapshot())
    }

    @Test fun clampsToUnitRange() {
        val h = LevelHistory(capacity = 4)
        h.push(-1f); h.push(2f); h.push(Float.NaN)
        assertEquals(listOf(0f, 1f, 0f), h.snapshot())
    }

    @Test fun clearEmpties() {
        val h = LevelHistory(capacity = 2)
        h.push(0.5f)
        h.clear()
        assertTrue(h.snapshot().isEmpty())
    }
}
