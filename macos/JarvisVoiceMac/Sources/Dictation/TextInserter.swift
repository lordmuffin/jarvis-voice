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
        guard let source = CGEventSource(stateID: .privateState) else { return false }
        source.userData = eventTag
        let key = CGKeyCode(kVK_ANSI_V)
        guard let down = CGEvent(keyboardEventSource: source, virtualKey: key, keyDown: true),
              let up = CGEvent(keyboardEventSource: source, virtualKey: key, keyDown: false) else { return false }
        down.flags = .maskCommand
        up.flags = .maskCommand
        down.post(tap: .cghidEventTap)
        up.post(tap: .cghidEventTap)
        return true
    }
}
