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

/// The shared pill: surface, hairline border, fully rounded, 52pt tall. Listening, Transcribing, the
/// live tail, Copied and the error states all use it; only the border colour and contents change.
struct DictationPill: View {
    let dictation: DictationController

    var body: some View {
        let content = HUDContent.make(
            state: dictation.state, message: dictation.message,
            liveText: dictation.liveText, assetProgress: dictation.assetProgress
        )
        VStack {
            Spacer(minLength: 0)
            HStack(alignment: .center, spacing: 12) {
                if content.spinner {
                    InstSpinner().frame(width: 14, height: 14)
                } else if content.meter {
                    InstDot(size: 10, breathing: true)
                }
                if let tail = content.tail, content.tone == .active {
                    Text(tail)
                        .instStyle(InstType.body)
                        .lineLimit(1)
                        .truncationMode(.head)
                        .foregroundStyle(Color.inst.text)
                } else {
                    Text(content.label)
                        .instStyle(InstType.tag)
                        .foregroundStyle(labelColor(content.tone))
                    if let note = content.tail {
                        Text(note).instStyle(InstType.caption).foregroundStyle(Color.inst.text).lineLimit(2)
                    }
                }
                if content.meter { InstMeter(height: 18) }
            }
            .padding(.horizontal, 20)
            .frame(minHeight: InstSize.livePillHud)
            .background(Capsule().fill(Color.inst.surface))
            .overlay(Capsule().stroke(border(content.tone), lineWidth: 1))
            .shadow(color: .black.opacity(0.3), radius: 10, y: 4)
            .frame(maxWidth: 480)
            .fixedSize(horizontal: false, vertical: true)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(content.tail.map { "\(content.label) \($0)" } ?? content.label)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(.bottom, 12)
        .animation(.easeOut(duration: 0.15), value: dictation.liveText)
    }

    private func border(_ tone: HUDContent.Tone) -> Color {
        switch tone {
        case .active: Color.inst.accent
        case .quiet: Color.inst.control
        case .done: Color.inst.line
        case .error: Color.inst.error
        case .notice: Color.inst.warning
        }
    }

    private func labelColor(_ tone: HUDContent.Tone) -> Color {
        switch tone {
        case .active: Color.inst.accent
        case .quiet: Color.inst.text2
        case .done: Color.inst.success
        case .error: Color.inst.error
        case .notice: Color.inst.warning
        }
    }
}
