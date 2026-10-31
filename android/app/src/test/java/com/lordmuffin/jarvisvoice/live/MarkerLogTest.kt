package com.lordmuffin.jarvisvoice.live

import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

/** Markers are the point of a drive recording, so they must outlive the process. */
class MarkerLogTest {
    @get:Rule val tmp = TemporaryFolder()

    @Test fun roundTripsInOrderAcrossInstances() {
        val dir = tmp.newFolder("s1")
        val log = MarkerLog.inDir(dir)
        log.append(LocalMarkdown.Marker(1_200, "mark"))
        log.append(LocalMarkdown.Marker(5_000, "muted"))
        assertEquals(
            listOf(LocalMarkdown.Marker(1_200, "mark"), LocalMarkdown.Marker(5_000, "muted")),
            MarkerLog.inDir(dir).read(),
        )
    }

    @Test fun emptyWhenThereIsNoFile() {
        assertEquals(emptyList<LocalMarkdown.Marker>(), MarkerLog.inDir(tmp.newFolder("s2")).read())
    }

    /** A kill mid-write leaves a torn last line; everything before it is still good. */
    @Test fun skipsATornLastLine() {
        val dir = tmp.newFolder("s3")
        MarkerLog.inDir(dir).append(LocalMarkdown.Marker(100, "mark"))
        File(dir, "markers.jsonl").appendText("{\"t_ms\":200,\"lab")
        assertEquals(listOf(LocalMarkdown.Marker(100, "mark")), MarkerLog.inDir(dir).read())
    }

    @Test fun labelsWithQuotesAndNewlinesSurvive() {
        val dir = tmp.newFolder("s4")
        val odd = LocalMarkdown.Marker(7, "say \"hi\"\nnext")
        MarkerLog.inDir(dir).append(odd)
        assertEquals(listOf(odd), MarkerLog.inDir(dir).read())
    }
}
