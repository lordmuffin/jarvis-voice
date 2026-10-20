import AppKit
import SwiftUI

/// Non-activating floating window so it never steals focus from the call.
@MainActor
final class PanelController {
    static let collapsedHeight: CGFloat = 96
    private static let expandedHeightKey = "panelExpandedHeight"

    private weak var model: AppModel?
    private var panel: NSPanel?

    init(model: AppModel) { self.model = model }

    func show() {
        guard let model else { return }
        let panel = self.panel ?? makePanel(model: model)
        self.panel = panel
        panel.level = model.settings.panelAlwaysOnTop ? .floating : .normal
        panel.orderFrontRegardless()
    }

    func hide() { panel?.orderOut(nil) }

    /// Shrinks the panel to the recording indicator and the newest suggestion, or restores the size it
    /// had. The top edge stays where it is.
    func setCollapsed(_ collapsed: Bool) {
        guard let panel else { return }
        var frame = panel.frame
        let target: CGFloat
        if collapsed {
            if frame.height > Self.collapsedHeight + 1 {
                UserDefaults.standard.set(frame.height, forKey: Self.expandedHeightKey)
            }
            target = Self.collapsedHeight
        } else {
            let saved = UserDefaults.standard.double(forKey: Self.expandedHeightKey)
            target = saved > Self.collapsedHeight ? saved : 640
        }
        frame.origin.y += frame.height - target
        frame.size.height = target
        panel.setFrame(frame, display: true, animate: true)
    }

    private func makePanel(model: AppModel) -> NSPanel {
        let panel = NSPanel(
            contentRect: NSRect(x: 0, y: 0, width: 480, height: 640),
            styleMask: [.titled, .closable, .resizable, .utilityWindow, .nonactivatingPanel, .fullSizeContentView],
            backing: .buffered, defer: false
        )
        panel.title = "Jarvis Live"
        panel.titleVisibility = .hidden
        panel.isFloatingPanel = true
        panel.hidesOnDeactivate = false
        panel.becomesKeyOnlyIfNeeded = true
        panel.isReleasedWhenClosed = false
        panel.titlebarAppearsTransparent = true
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        panel.setFrameAutosaveName("JarvisCopilotPanel")
        panel.contentView = NSHostingView(rootView: CopilotPanelView(model: model))
        panel.center()
        return panel
    }
}
