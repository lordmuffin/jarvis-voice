import Foundation

/// The modifier key that triggers dictation on its own (no chord).
enum TriggerKey: String, CaseIterable, Identifiable, Sendable {
    case off, rightCommand, rightOption, rightControl, rightShift

    var id: String { rawValue }

    var title: String {
        switch self {
        case .off: "Off"
        case .rightCommand: "Right \u{2318}"
        case .rightOption: "Right \u{2325}"
        case .rightControl: "Right \u{2303}"
        case .rightShift: "Right \u{21E7}"
        }
    }

    /// Virtual key code reported on `.flagsChanged` (`kVK_RightCommand` etc.).
    var keyCode: UInt16? {
        switch self {
        case .off: nil
        case .rightCommand: 54
        case .rightOption: 61
        case .rightControl: 62
        case .rightShift: 60
        }
    }

    /// Device-dependent modifier bit (`NX_DEVICER*KEYMASK`), set while this physical key is down.
    /// Unlike `.command`, it stays clear when only the left-hand key is held.
    var deviceMask: UInt {
        switch self {
        case .off: 0
        case .rightCommand: 0x10
        case .rightOption: 0x40
        case .rightControl: 0x2000
        case .rightShift: 0x04
        }
    }
}

enum TriggerMode: String, CaseIterable, Identifiable, Sendable {
    case hold, tap, doubleTap

    var id: String { rawValue }
    var title: String {
        switch self {
        case .hold: "Hold to talk"
        case .tap: "Tap to start, tap to stop"
        case .doubleTap: "Double-tap to start, tap to stop"
        }
    }
}

/// Turns presses of a lone modifier key into dictation actions, without AppKit so it can be tested.
///
/// The key only counts when nothing else happens while it is down: any other key, modifier or
/// mouse button makes the press a chord (⌘C, ⌘-click), which never starts dictation and cancels a
/// hold that already began. Whether dictation is running is the controller's business, so a tap
/// reports `.toggle` (or, in double-tap mode, `.tap` and `.doubleTap`) and the controller decides
/// between starting and stopping.
struct DictationTrigger: Sendable {
    enum Action: Equatable, Sendable { case begin, commit, cancel, toggle, tap, doubleTap }

    /// Hold mode starts listening only after this long, so ordinary ⌘ shortcuts never open the mic.
    static let holdThreshold: TimeInterval = 0.2
    /// Tap mode: a longer press is not a tap.
    static let tapMaxDuration: TimeInterval = 0.35
    /// Double-tap mode: the longest gap between releasing the first tap and pressing the second.
    static let doubleTapGap: TimeInterval = 0.4
    /// Timer slack when `tick` fires right at the threshold.
    private static let tolerance: TimeInterval = 0.01

    let key: TriggerKey
    let mode: TriggerMode
    private(set) var pressedAt: TimeInterval?
    private var chorded = false
    private var began = false
    /// Double-tap mode: when the last lone tap was released, while it can still open a double tap.
    private var lastTapAt: TimeInterval?

    init(key: TriggerKey, mode: TriggerMode) {
        self.key = key
        self.mode = mode
    }

    var isPressed: Bool { pressedAt != nil }

    mutating func keyDown(at time: TimeInterval) -> Action? {
        guard key != .off, pressedAt == nil else { return nil }
        pressedAt = time
        chorded = false
        began = false
        return nil
    }

    /// Called once the hold threshold has passed since `keyDown`.
    mutating func tick(at time: TimeInterval) -> Action? {
        guard mode == .hold, let pressedAt, !chorded, !began,
              time - pressedAt >= Self.holdThreshold - Self.tolerance else { return nil }
        began = true
        return .begin
    }

    mutating func keyUp(at time: TimeInterval) -> Action? {
        guard let pressedAt else { return nil }
        self.pressedAt = nil
        switch mode {
        case .hold:
            defer { began = false }
            return began ? .commit : nil
        case .tap:
            return !chorded && time - pressedAt <= Self.tapMaxDuration ? .toggle : nil
        case .doubleTap:
            guard !chorded, time - pressedAt <= Self.tapMaxDuration else {
                lastTapAt = nil
                return nil
            }
            if let lastTapAt, pressedAt - lastTapAt <= Self.doubleTapGap {
                self.lastTapAt = nil
                return .doubleTap
            }
            lastTapAt = time
            return .tap
        }
    }

    /// Any other key, modifier or mouse button. Between two taps it also breaks the double tap.
    mutating func otherInput() -> Action? {
        lastTapAt = nil
        guard pressedAt != nil else { return nil }
        chorded = true
        guard began else { return nil }
        began = false
        return .cancel
    }

    /// Esc cancels dictation in either mode.
    mutating func escape() -> Action? {
        _ = otherInput()
        return .cancel
    }
}
