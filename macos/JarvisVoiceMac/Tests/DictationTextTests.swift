import JarvisLiveKit
import XCTest

@testable import Jarvis_Voice

final class DictationTextTests: XCTestCase {
    private func segment(_ startMs: Int, _ text: String, final: Bool = true) -> DraftSegment {
        DraftSegment(channel: .mic, startMs: startMs, endMs: startMs + 500, text: text, final: final)
    }

    func testJoinsFinalsInTimeOrder() {
        let text = DictationText.join([
            segment(2_000, "second part."),
            segment(0, "First part,"),
        ])
        XCTAssertEqual(text, "First part, second part.")
    }

    func testIgnoresVolatileSegments() {
        XCTAssertEqual(DictationText.join([segment(0, "Kept."), segment(900, "guess", final: false)]), "Kept.")
    }

    func testCollapsesWhitespaceAndTrims() {
        XCTAssertEqual(DictationText.join([segment(0, "  Hello \n  there  "), segment(10, "\tfriend ")]), "Hello there friend")
    }

    func testEmptyWhenNothingWasHeard() {
        XCTAssertEqual(DictationText.join([]), "")
        XCTAssertEqual(DictationText.join([segment(0, "   ")]), "")
    }

    func testTailKeepsTheEnd() {
        XCTAssertEqual(DictationText.tail("short"), "short")
        let long = String(repeating: "a", count: 50) + " " + String(repeating: "b", count: 50)
        let tail = DictationText.tail(long, maxLength: 20)
        XCTAssertEqual(tail.count, 20)
        XCTAssertTrue(tail.hasPrefix("\u{2026}"))
        XCTAssertTrue(tail.hasSuffix("bbbb"))
    }
}
