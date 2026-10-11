import XCTest

@testable import Jarvis_Voice

final class DictationTriggerTests: XCTestCase {
    private let hold = DictationTrigger.holdThreshold
    private let tapMax = DictationTrigger.tapMaxDuration
    private let gap = DictationTrigger.doubleTapGap

    // MARK: Hold

    func testHoldBeginsAfterThresholdAndCommitsOnRelease() {
        var trigger = DictationTrigger(key: .rightCommand, mode: .hold)
        XCTAssertNil(trigger.keyDown(at: 10))
        XCTAssertEqual(trigger.tick(at: 10 + hold), .begin)
        XCTAssertNil(trigger.tick(at: 10 + hold + 1), "begins once per press")
        XCTAssertEqual(trigger.keyUp(at: 13), .commit)
    }

    func testHoldReleasedBeforeThresholdDoesNothing() {
        var trigger = DictationTrigger(key: .rightCommand, mode: .hold)
        _ = trigger.keyDown(at: 10)
        XCTAssertNil(trigger.keyUp(at: 10.1))
        XCTAssertNil(trigger.tick(at: 10 + hold), "a late timer after release is ignored")
    }

    func testShortcutBeforeThresholdNeverBegins() {
        var trigger = DictationTrigger(key: .rightCommand, mode: .hold)
        _ = trigger.keyDown(at: 10)
        XCTAssertNil(trigger.otherInput(), "⌘C before anything started")
        XCTAssertNil(trigger.tick(at: 10 + hold))
        XCTAssertNil(trigger.keyUp(at: 11))
    }

    func testShortcutDuringHoldCancels() {
        var trigger = DictationTrigger(key: .rightCommand, mode: .hold)
        _ = trigger.keyDown(at: 10)
        XCTAssertEqual(trigger.tick(at: 10 + hold), .begin)
        XCTAssertEqual(trigger.otherInput(), .cancel)
        XCTAssertNil(trigger.otherInput())
        XCTAssertNil(trigger.keyUp(at: 12), "nothing to commit after a cancel")
    }

    func testEscapeCancels() {
        var trigger = DictationTrigger(key: .rightCommand, mode: .hold)
        _ = trigger.keyDown(at: 10)
        _ = trigger.tick(at: 10 + hold)
        XCTAssertEqual(trigger.escape(), .cancel)
        XCTAssertNil(trigger.keyUp(at: 12))
    }

    func testOtherInputWhileKeyIsUpIsIgnored() {
        var trigger = DictationTrigger(key: .rightCommand, mode: .hold)
        XCTAssertNil(trigger.otherInput())
        XCTAssertNil(trigger.keyUp(at: 1), "release without a press")
    }

    func testNextPressStartsClean() {
        var trigger = DictationTrigger(key: .rightCommand, mode: .hold)
        _ = trigger.keyDown(at: 10)
        _ = trigger.otherInput()
        _ = trigger.keyUp(at: 10.5)
        _ = trigger.keyDown(at: 20)
        XCTAssertEqual(trigger.tick(at: 20 + hold), .begin, "the earlier chord doesn't stick")
    }

    // MARK: Tap

    func testTapToggles() {
        var trigger = DictationTrigger(key: .rightOption, mode: .tap)
        XCTAssertNil(trigger.keyDown(at: 10))
        XCTAssertNil(trigger.tick(at: 10 + hold), "tap mode ignores the hold timer")
        XCTAssertEqual(trigger.keyUp(at: 10.1), .toggle)
        _ = trigger.keyDown(at: 15)
        XCTAssertEqual(trigger.keyUp(at: 15.1), .toggle)
    }

    func testLongPressIsNotATap() {
        var trigger = DictationTrigger(key: .rightCommand, mode: .tap)
        _ = trigger.keyDown(at: 10)
        XCTAssertNil(trigger.keyUp(at: 10 + tapMax + 0.05))
    }

    func testChordIsNotATap() {
        var trigger = DictationTrigger(key: .rightCommand, mode: .tap)
        _ = trigger.keyDown(at: 10)
        XCTAssertNil(trigger.otherInput())
        XCTAssertNil(trigger.keyUp(at: 10.1))
    }

    // MARK: Double tap

    func testDoubleTapReportsTapThenDoubleTap() {
        var trigger = DictationTrigger(key: .rightCommand, mode: .doubleTap)
        XCTAssertNil(trigger.keyDown(at: 10))
        XCTAssertNil(trigger.tick(at: 10 + hold), "double-tap mode ignores the hold timer")
        XCTAssertEqual(trigger.keyUp(at: 10.1), .tap)
        _ = trigger.keyDown(at: 10.1 + gap - 0.05)
        XCTAssertEqual(trigger.keyUp(at: 10.2 + gap - 0.05), .doubleTap)
    }

    func testTapAfterDoubleTapIsASingleTap() {
        var trigger = DictationTrigger(key: .rightCommand, mode: .doubleTap)
        _ = trigger.keyDown(at: 10)
        _ = trigger.keyUp(at: 10.1)
        _ = trigger.keyDown(at: 10.2)
        XCTAssertEqual(trigger.keyUp(at: 10.3), .doubleTap)
        _ = trigger.keyDown(at: 10.4)
        XCTAssertEqual(trigger.keyUp(at: 10.5), .tap, "a double tap doesn't open another one")
        _ = trigger.keyDown(at: 10.6)
        XCTAssertEqual(trigger.keyUp(at: 10.7), .doubleTap)
    }

    func testSlowSecondTapIsASingleTap() {
        var trigger = DictationTrigger(key: .rightCommand, mode: .doubleTap)
        _ = trigger.keyDown(at: 10)
        XCTAssertEqual(trigger.keyUp(at: 10.1), .tap)
        _ = trigger.keyDown(at: 10.1 + gap + 0.05)
        XCTAssertEqual(trigger.keyUp(at: 10.2 + gap + 0.05), .tap)
    }

    func testLongPressBreaksADoubleTap() {
        var trigger = DictationTrigger(key: .rightCommand, mode: .doubleTap)
        _ = trigger.keyDown(at: 10)
        _ = trigger.keyUp(at: 10.1)
        _ = trigger.keyDown(at: 10.2)
        XCTAssertNil(trigger.keyUp(at: 10.2 + tapMax + 0.05))
        _ = trigger.keyDown(at: 10.7)
        XCTAssertEqual(trigger.keyUp(at: 10.8), .tap, "the long press doesn't leave a tap behind")
    }

    func testInputBetweenTapsBreaksADoubleTap() {
        var trigger = DictationTrigger(key: .rightCommand, mode: .doubleTap)
        _ = trigger.keyDown(at: 10)
        _ = trigger.keyUp(at: 10.1)
        XCTAssertNil(trigger.otherInput(), "typing a letter after the tap")
        _ = trigger.keyDown(at: 10.2)
        XCTAssertEqual(trigger.keyUp(at: 10.3), .tap)
    }

    func testChordIsNotADoubleTap() {
        var trigger = DictationTrigger(key: .rightCommand, mode: .doubleTap)
        _ = trigger.keyDown(at: 10)
        _ = trigger.keyUp(at: 10.1)
        _ = trigger.keyDown(at: 10.2)
        XCTAssertNil(trigger.otherInput(), "⌘C right after a tap")
        XCTAssertNil(trigger.keyUp(at: 10.3))
    }

    func testEscapeCancelsInDoubleTapMode() {
        var trigger = DictationTrigger(key: .rightCommand, mode: .doubleTap)
        _ = trigger.keyDown(at: 10)
        _ = trigger.keyUp(at: 10.1)
        XCTAssertEqual(trigger.escape(), .cancel)
        _ = trigger.keyDown(at: 10.2)
        XCTAssertEqual(trigger.keyUp(at: 10.3), .tap, "Esc also breaks a double tap")
    }

    // MARK: Keys

    func testOffNeverFires() {
        var trigger = DictationTrigger(key: .off, mode: .hold)
        XCTAssertNil(trigger.keyDown(at: 10))
        XCTAssertNil(trigger.tick(at: 11))
        XCTAssertNil(trigger.keyUp(at: 12))
        XCTAssertNil(TriggerKey.off.keyCode)
    }

    func testRightHandKeyCodesAndMasks() {
        XCTAssertEqual(TriggerKey.rightCommand.keyCode, 54)
        XCTAssertEqual(TriggerKey.rightOption.keyCode, 61)
        XCTAssertEqual(TriggerKey.rightControl.keyCode, 62)
        XCTAssertEqual(TriggerKey.rightShift.keyCode, 60)
        let masks = TriggerKey.allCases.filter { $0 != .off }.map(\.deviceMask)
        XCTAssertEqual(Set(masks).count, masks.count, "each key has its own device bit")
    }
}
