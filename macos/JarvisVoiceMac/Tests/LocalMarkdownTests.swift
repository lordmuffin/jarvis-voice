import JarvisLiveKit
import XCTest

@testable import Jarvis_Voice

final class LocalMarkdownTests: XCTestCase {
    private let started = Date(timeIntervalSince1970: 1_790_000_000)

    func testRenderThenParseRoundTripsTheHeader() throws {
        let header = LocalMarkdown.Header(
            sessionId: "0b1f6d2e-7e0a-4c53-9d3f-2a5e0a6c1d11", mode: .meeting,
            channels: [.mic, .system], started: started, uploadedAs: nil
        )
        let text = LocalMarkdown.render(
            header: header,
            lines: [
                .init(channel: .system, startMs: 5_000, text: "Hello there."),
                .init(channel: .mic, startMs: 1_000, text: "Hi."),
            ],
            markers: [(tMs: 3_000, label: "Marker 1")]
        )
        XCTAssertEqual(LocalMarkdown.parseHeader(text), header)
        // Ordered by time, Me/Them labelled, markers listed.
        let me = try XCTUnwrap(text.range(of: "**Me** `00:01` Hi."))
        let them = try XCTUnwrap(text.range(of: "**Them** `00:05` Hello there."))
        XCTAssertLessThan(me.lowerBound, them.lowerBound)
        XCTAssertTrue(text.contains("- `00:03` Marker 1"))
    }

    func testMarkUploadedRewritesOnlyTheFlag() throws {
        let header = LocalMarkdown.Header(
            sessionId: "s", mode: .solo, channels: [.mic], started: started, uploadedAs: nil
        )
        let text = LocalMarkdown.render(header: header, lines: [], markers: [])
        let updated = LocalMarkdown.markUploaded(text, serverSessionId: "server-id")
        XCTAssertEqual(LocalMarkdown.parseHeader(updated)?.uploadedAs, "server-id")
        XCTAssertEqual(LocalMarkdown.parseHeader(updated)?.sessionId, "s")
    }

    func testParseRejectsTextWithoutFrontmatter() {
        XCTAssertNil(LocalMarkdown.parseHeader("# just a note"))
    }

    func testClockFormatsMinutesAndSeconds() {
        XCTAssertEqual(LocalMarkdown.clock(0), "00:00")
        XCTAssertEqual(LocalMarkdown.clock(61_999), "01:01")
        XCTAssertEqual(LocalMarkdown.clock(-5), "00:00")
    }
}
