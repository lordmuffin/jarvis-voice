import JarvisLiveKit
import XCTest

@testable import Jarvis_Voice

/// What the panel's ask strip says in each state (docs/plans/voice-push-to-talk.md).
final class AskPresentationTests: XCTestCase {
    func testHiddenShowsNothing() {
        XCTAssertNil(AskPresentation.line(for: .hidden, localOnly: false))
    }

    func testLocalOnlySaysWhy() {
        XCTAssertEqual(
            AskPresentation.line(for: .hidden, localOnly: true),
            AskLine(chip: "LOCAL ONLY", tone: .neutral, text: "Jarvis can't hear or speak in a local-only session.")
        )
    }

    func testReadyNamesTheKey() {
        XCTAssertEqual(AskPresentation.line(for: .ready, localOnly: false)?.chip, "HOLD ⌃⌥A TO ASK")
    }

    func testSpeakingShowsTheReply() {
        XCTAssertEqual(
            AskPresentation.line(for: .speaking("Priya runs it."), localOnly: false),
            AskLine(chip: "SPEAKING", tone: .live, text: "Priya runs it.")
        )
    }

    func testTextOnlySaysWhyItWasNotSpoken() {
        let line = AskPresentation.line(for: .answered("Priya runs it.", spoken: false, why: .route), localOnly: false)
        XCTAssertEqual(line?.chip, "TEXT ONLY")
        XCTAssertEqual(line?.text, "Priya runs it. Not spoken: no headphones.")
    }

    func testNothingHeard() {
        XCTAssertEqual(
            AskPresentation.line(for: .answered("", spoken: false, why: .nothingHeard), localOnly: false)?.text,
            "Nothing heard."
        )
    }
}
