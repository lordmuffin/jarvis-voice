import AppKit
import ApplicationServices
import Carbon.HIToolbox

/// Puts dictated text on the clipboard and pastes it into the frontmost app with a synthetic ⌘V.
///
/// Posting key events needs Accessibility permission (the same grant the trigger key's global
/// monitor needs). Without it the text is still copied.
@MainActor
enum TextInserter {
    /// Stamped on our synthetic events (`eventSourceUserData`) so the key monitor can ignore them.
    static let eventTag: Int64 = 0x4A56_4443  // 'JVDC'

    static var isTrusted: Bool { AXIsProcessTrusted() }

    /// Shows the system Accessibility prompt if the app is not trusted yet.
    static func requestTrust() {
        // The value of `kAXTrustedCheckOptionPrompt`, spelled out to avoid the C global.
        let options = ["AXTrustedCheckOptionPrompt": true] as CFDictionary
        _ = AXIsProcessTrustedWithOptions(options)
    }

    static func openAccessibilitySettings() {
        if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility") {
            NSWorkspace.shared.open(url)
        }
    }

    static func copy(_ text: String) {
        let pasteboard = NSPasteboard.general
        pasteboard.clearContents()
        pasteboard.setString(text, forType: .string)
    }

    /// One synthetic key event of the paste chord.
    struct KeyStroke: Equatable {
        let key: CGKeyCode
        let down: Bool
        let flags: CGEventFlags
    }

    /// ⌘ with the left-hand key's device bit (`NX_DEVICELCMDKEYMASK`), as a real left ⌘ reports it.
    private static let leftCommand = CGEventFlags(rawValue: CGEventFlags.maskCommand.rawValue | 0x08)

    /// ⌘V as a real chord: ⌘ down, V down and up, ⌘ up. A lone V that only carries the ⌘ flag is
    /// ignored by apps that read the live modifier state, so the text was copied but never pasted.
    static let pasteChord: [KeyStroke] = [
        KeyStroke(key: CGKeyCode(kVK_Command), down: true, flags: leftCommand),
        KeyStroke(key: CGKeyCode(kVK_ANSI_V), down: true, flags: leftCommand),
        KeyStroke(key: CGKeyCode(kVK_ANSI_V), down: false, flags: leftCommand),
        KeyStroke(key: CGKeyCode(kVK_Command), down: false, flags: []),
    ]

    /// Sends ⌘V once the user's own modifiers are up, so they don't turn it into another shortcut.
    /// Returns `false` if the app may not post events.
    @discardableResult
    static func paste() async -> Bool {
        guard isTrusted else { return false }
        var waits = 0
        while waits < 20, !NSEvent.modifierFlags.isDisjoint(with: [.command, .option, .control, .shift]) {
            try? await Task.sleep(for: .milliseconds(25))
            waits += 1
        }
        // The HID system state, so our ⌘ down is part of the modifier state the target app sees.
        guard let source = CGEventSource(stateID: .hidSystemState) else { return false }
        source.userData = eventTag
        // Built up front, so a failure can't leave ⌘ held down.
        var events: [CGEvent] = []
        for stroke in pasteChord {
            guard let event = CGEvent(keyboardEventSource: source, virtualKey: stroke.key, keyDown: stroke.down)
            else { return false }
            event.flags = stroke.flags
            event.setIntegerValueField(.eventSourceUserData, value: eventTag)
            events.append(event)
        }
        // A short gap between strokes; some apps drop a chord that arrives all at once.
        for event in events {
            event.post(tap: .cghidEventTap)
            try? await Task.sleep(for: .milliseconds(10))
        }
        return true
    }
}
