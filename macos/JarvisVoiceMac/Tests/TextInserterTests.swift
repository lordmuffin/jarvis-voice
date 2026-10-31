import Carbon.HIToolbox
import CoreGraphics
import XCTest

@testable import Jarvis_Voice

@MainActor
final class TextInserterTests: XCTestCase {
    private let command = CGKeyCode(kVK_Command)
    private let v = CGKeyCode(kVK_ANSI_V)

    func testPastePressesARealCommandKeyAroundV() {
        let chord = TextInserter.pasteChord
        XCTAssertEqual(chord.map(\.key), [command, v, v, command])
        XCTAssertEqual(chord.map(\.down), [true, true, false, false])
    }

    func testVCarriesCommandAndCommandUpClearsIt() {
        let chord = TextInserter.pasteChord
        for stroke in chord.dropLast() {
            XCTAssertTrue(stroke.flags.contains(.maskCommand), "\(stroke)")
        }
        XCTAssertFalse(chord.last!.flags.contains(.maskCommand))
    }

    func testCommandIsTheLeftKeySoItNeverLooksLikeTheTrigger() {
        let chord = TextInserter.pasteChord
        XCTAssertNotEqual(command, CGKeyCode(TriggerKey.rightCommand.keyCode!))
        for stroke in chord where stroke.flags.contains(.maskCommand) {
            XCTAssertEqual(stroke.flags.rawValue & UInt64(TriggerKey.rightCommand.deviceMask), 0, "\(stroke)")
        }
    }
}
