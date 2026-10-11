import AppKit
import SwiftUI

/// The dictation pill near the bottom of the screen. It never becomes key or takes clicks, so the
/// app the user is typing in stays frontmost and receives the paste.
@MainActor
final class DictationHUD {
    private static let size = NSSize(width: 520, height: 120)

    private weak var dictation: DictationController?
    private var panel: NSPanel?

    init(dictation: DictationController) { self.dictation = dictation }

    func show() {
        guard let dictation else { return }
        let panel = self.panel ?? makePanel(dictation: dictation)
        self.panel = panel
        if !panel.isVisible { position(panel) }
        panel.orderFrontRegardless()
    }

    func hide() { panel?.orderOut(nil) }

    private func makePanel(dictation: DictationController) -> NSPanel {
        let panel = NSPanel(
            contentRect: NSRect(origin: .zero, size: Self.size),
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered, defer: false
        )
        panel.level = .statusBar
        panel.isFloatingPanel = true
        panel.hidesOnDeactivate = false
        panel.becomesKeyOnlyIfNeeded = true
        panel.ignoresMouseEvents = true
        panel.isReleasedWhenClosed = false
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = false
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary, .ignoresCycle]
        panel.contentView = NSHostingView(rootView: DictationPill(dictation: dictation))
        return panel
    }

    /// Bottom centre of the screen the pointer is on, which is usually where the user is typing.
    private func position(_ panel: NSPanel) {
        let mouse = NSEvent.mouseLocation
        let screen = NSScreen.screens.first { NSMouseInRect(mouse, $0.frame, false) } ?? NSScreen.main
        guard let frame = screen?.visibleFrame else { return }
        panel.setFrameOrigin(NSPoint(x: frame.midX - Self.size.width / 2, y: frame.minY + 48))
    }
}

struct DictationPill: View {
    let dictation: DictationController

    var body: some View {
        VStack {
            Spacer(minLength: 0)
            HStack(alignment: .center, spacing: 10) {
                Image(systemName: symbol)
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundStyle(tint)
                    .symbolEffect(.pulse, isActive: dictation.state == .listening)
                VStack(alignment: .leading, spacing: 2) {
                    Text(title).font(.caption.weight(.semibold)).foregroundStyle(JV.text2)
                    if dictation.message == nil, !dictation.liveText.isEmpty {
                        Text(DictationText.tail(dictation.liveText))
                            .font(.callout)
                            .lineLimit(2)
                    }
                }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 10)
            .background(RoundedRectangle(cornerRadius: 18, style: .continuous).fill(JV.surface))
            .overlay(RoundedRectangle(cornerRadius: 18, style: .continuous).stroke(JV.border))
            .shadow(color: .black.opacity(0.35), radius: 10, y: 4)
            .frame(maxWidth: 480)
            .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(.bottom, 12)
        .foregroundStyle(JV.text)
        .preferredColorScheme(.dark)
        .animation(.easeOut(duration: 0.15), value: dictation.liveText)
    }

    private var title: String {
        if let message = dictation.message { return message }
        if dictation.state != .idle, let progress = dictation.assetProgress {
            return "Installing speech model \(Int(progress * 100))%"
        }
        switch dictation.state {
        case .listening: return "Listening\u{2026}"
        case .finishing: return "Transcribing\u{2026}"
        case .idle: return ""
        }
    }

    private var symbol: String {
        if dictation.message != nil { return "info.circle.fill" }
        return dictation.state == .finishing ? "waveform" : "mic.fill"
    }

    private var tint: Color {
        if dictation.message != nil { return JV.text2 }
        return dictation.state == .finishing ? JV.text2 : JV.accent
    }
}
