import JarvisLiveKit
import XCTest

@testable import Jarvis_Voice

final class PresentationLogicTests: XCTestCase {
    // MARK: Menu-bar icon

    func testIdleIconNamesStateAndChannel() {
        let spec = MenuBarIconSpec.make(isRecording: false, isDictating: false, elapsedSeconds: 0, channel: .lab)
        XCTAssertEqual(spec.glyph, .waveform)
        XCTAssertEqual(spec.badge, "LAB")
        XCTAssertTrue(spec.badgeFilled)
        XCTAssertEqual(spec.accessibilityLabel, "Jarvis, idle, LAB")
    }

    func testLiveBadgeIsOutlined() {
        let spec = MenuBarIconSpec.make(isRecording: false, isDictating: false, elapsedSeconds: 0, channel: .live)
        XCTAssertFalse(spec.badgeFilled)
        XCTAssertEqual(spec.badge, "LIVE")
    }

    func testRecordingWinsOverDictationAndShowsElapsed() {
        let spec = MenuBarIconSpec.make(isRecording: true, isDictating: true, elapsedSeconds: 1122, channel: .live)
        XCTAssertEqual(spec.glyph, .recording(elapsed: "18:42"))
        XCTAssertEqual(spec.accessibilityLabel, "Jarvis, recording 18:42, LIVE")
    }

    /// The badge says which server a streaming session talks to; a local session talks to none, so
    /// it must never wear LIVE or LAB.
    func testLocalSessionShowsLocalNotTheServerChannel() {
        let spec = MenuBarIconSpec.make(
            isRecording: true, isDictating: false, elapsedSeconds: 65, channel: .live, localOnly: true
        )
        XCTAssertEqual(spec.glyph, .recording(elapsed: "01:05"))
        XCTAssertEqual(spec.badge, "LOCAL")
        XCTAssertTrue(spec.badgeFilled)
        XCTAssertEqual(spec.accessibilityLabel, "Jarvis, recording 01:05, local only, not streaming")
    }

    func testLocalSessionShowsLocalEvenWithoutAServerAddress() {
        let spec = MenuBarIconSpec.make(
            isRecording: true, isDictating: false, elapsedSeconds: 0, channel: nil, localOnly: true
        )
        XCTAssertEqual(spec.badge, "LOCAL")
    }

    func testLocalFlagIsIgnoredWhenNotRecording() {
        let spec = MenuBarIconSpec.make(
            isRecording: false, isDictating: false, elapsedSeconds: 0, channel: .live, localOnly: true
        )
        XCTAssertEqual(spec.badge, "LIVE")
    }

    func testDictatingAndNoServer() {
        let spec = MenuBarIconSpec.make(isRecording: false, isDictating: true, elapsedSeconds: 0, channel: nil)
        XCTAssertEqual(spec.glyph, .dictating)
        XCTAssertNil(spec.badge)
        XCTAssertEqual(spec.accessibilityLabel, "Jarvis, dictating")
    }

    func testElapsedFormat() {
        XCTAssertEqual(ClockFormat.elapsed(seconds: 5), "00:05")
        XCTAssertEqual(ClockFormat.elapsed(seconds: 3723), "1:02:03")
        XCTAssertEqual(ClockFormat.stamp(ms: 1_122_999), "18:42")
    }

    // MARK: Suggestions

    private func suggestion(_ id: String, expires: Int) -> CopilotSuggestion {
        CopilotSuggestion(id: id, kind: .question, text: id, expiresAtMs: expires)
    }

    func testAgeingFadesOverLastFiveSecondsWithAFloor() {
        XCTAssertEqual(SuggestionAgeing.opacity(remainingMs: 20_000), 1)
        XCTAssertEqual(SuggestionAgeing.opacity(remainingMs: 2500), 0.5, accuracy: 0.001)
        XCTAssertEqual(SuggestionAgeing.opacity(remainingMs: 10), 0.15, accuracy: 0.001)
        XCTAssertTrue(SuggestionAgeing.isAgeing(remainingMs: 4999))
        XCTAssertFalse(SuggestionAgeing.isAgeing(remainingMs: 5000))
    }

    func testLiveDropsDismissedAndExpired() {
        let all = [suggestion("a", expires: 1000), suggestion("b", expires: 9000), suggestion("c", expires: 9000)]
        let live = SuggestionAgeing.live(all, dismissed: ["c"], nowMs: 1000)
        XCTAssertEqual(live.map(\.id), ["b"])
    }

    func testNowDockPicksTheNewestAndCountsTheRest() {
        let split = SuggestionAgeing.split([suggestion("old", expires: 5000), suggestion("new", expires: 9000)])
        XCTAssertEqual(split.now?.id, "new")
        XCTAssertEqual(split.waiting.map(\.id), ["old"])
        XCTAssertNil(SuggestionAgeing.split([]).now)
    }

    // MARK: Layout

    func testPanelLayoutBreakpoints() {
        XCTAssertEqual(PanelLayout.forWidth(320), .strip)
        XCTAssertEqual(PanelLayout.forWidth(480), .standard)
        XCTAssertEqual(PanelLayout.forWidth(760), .twoColumn)
    }

    // MARK: New since last glance

    func testNothingIsNewBeforeTheFirstGlance() {
        let tracker = UnseenTracker()
        XCTAssertEqual(tracker.unseen(in: ["a", "b"]), [])
        XCTAssertNil(tracker.separatorIndex(in: ["a", "b"]))
    }

    func testRowsAfterAGlanceAreNewAndGetASeparator() {
        var tracker = UnseenTracker()
        tracker.glance(ids: ["a", "b"])
        XCTAssertEqual(tracker.unseen(in: ["a", "b", "c", "d"]), ["c", "d"])
        XCTAssertEqual(tracker.separatorIndex(in: ["a", "b", "c", "d"]), 2)
        tracker.glance(ids: ["a", "b", "c", "d"])
        XCTAssertEqual(tracker.unseen(in: ["a", "b", "c", "d"]), [])
    }

    // MARK: Feed

    func testFeedMergesMarkersByTime() {
        let lines = [
            TranscriptLine(id: "1", channel: .mic, speaker: .me, startMs: 0, endMs: 500, text: "hi", isDraft: false),
            TranscriptLine(id: "2", channel: .system, speaker: .them, startMs: 4000, endMs: 4500, text: "yo", isDraft: true),
        ]
        let rows = FeedBuilder.rows(lines: lines, markers: [(tMs: 2000, label: "Marker")])
        XCTAssertEqual(rows.map(\.id), ["1", "marker-0", "2"])
    }

    func testRelatedLinksOpenObsidianOnly() {
        XCTAssertNotNil(RelatedLink.url("obsidian://open?vault=v&file=n"))
        XCTAssertNil(RelatedLink.url("https://example.com"))
        XCTAssertNil(RelatedLink.url("file:///etc/passwd"))
    }

    // MARK: HUD

    func testHUDStates() {
        XCTAssertEqual(HUDContent.make(state: .listening, message: nil, liveText: "", assetProgress: nil).label, "LISTENING")
        let tail = HUDContent.make(state: .listening, message: nil, liveText: "size it against the node", assetProgress: nil)
        XCTAssertEqual(tail.tail, "size it against the node")
        XCTAssertTrue(tail.meter)
        let busy = HUDContent.make(state: .finishing, message: nil, liveText: "x", assetProgress: nil)
        XCTAssertEqual(busy.label, "TRANSCRIBING")
        XCTAssertTrue(busy.spinner)
        XCTAssertEqual(HUDContent.make(state: .idle, message: "Copied", liveText: "", assetProgress: nil).tone, .done)
        let miss = HUDContent.make(state: .idle, message: "Didn\u{2019}t catch that", liveText: "", assetProgress: nil)
        XCTAssertEqual(miss.label, "\u{2715} DIDN'T CATCH THAT")
        XCTAssertEqual(miss.tone, .error)
        let note = HUDContent.make(state: .idle, message: "Copied. Allow Accessibility in Settings to paste automatically.", liveText: "", assetProgress: nil)
        XCTAssertEqual(note.tone, .notice)
        XCTAssertEqual(note.tail, "Copied. Allow Accessibility in Settings to paste automatically.")
    }

    // MARK: Menu

    func testMenuStatusAndServerLines() {
        XCTAssertEqual(MenuText.status(link: .connected, isRecording: true), "\u{25CF} Live")
        XCTAssertEqual(MenuText.status(link: .idle, isRecording: false), "\u{25CB} Idle")
        XCTAssertEqual(MenuText.status(link: .offlineLocal, isRecording: true), "\u{25CB} Local-only")
        XCTAssertEqual(MenuText.status(link: .failed("boom"), isRecording: true), "\u{2715} Degraded: boom")
        XCTAssertEqual(
            MenuText.server(channel: .lab, host: "live.lab.apj.dev", version: "0.12.0"),
            "Server: LAB \u{00B7} live.lab.apj.dev (0.12.0)"
        )
        XCTAssertNil(MenuText.server(channel: nil, host: "x", version: nil))
    }

    func testCopilotKindsFollowTheCanonicalOrder() {
        // Actions, Decisions, Notes, Related: the order every Jarvis app shows.
        XCTAssertEqual(CopilotKind.allCases.map(\.rawValue), ["Actions", "Decisions", "Notes", "Related"])
        XCTAssertEqual(CopilotFilter.allCases.map(\.rawValue), ["All", "Actions", "Decisions", "Notes", "Related"])
    }

    func testMenuHeaderNamesTheChannel() {
        XCTAssertEqual(MenuText.header(version: "0.12.0", channel: .lab), "Jarvis Voice 0.12.0 \u{00B7} LAB")
        XCTAssertEqual(MenuText.header(version: "0.12.0", channel: .live), "Jarvis Voice 0.12.0 \u{00B7} LIVE")
        XCTAssertEqual(MenuText.header(version: "0.12.0", channel: nil), "Jarvis Voice 0.12.0")
        XCTAssertEqual(
            MenuText.server(channel: .live, host: "live.apj.dev", version: nil),
            "Server: LIVE \u{00B7} live.apj.dev"
        )
    }
}
