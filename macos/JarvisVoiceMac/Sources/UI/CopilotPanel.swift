import AppKit
import JarvisLiveKit
import SwiftUI

/// Non-activating floating window so it never steals focus from the call.
@MainActor
final class PanelController {
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

    private func makePanel(model: AppModel) -> NSPanel {
        let panel = NSPanel(
            contentRect: NSRect(x: 0, y: 0, width: 420, height: 640),
            styleMask: [.titled, .closable, .resizable, .utilityWindow, .nonactivatingPanel, .fullSizeContentView],
            backing: .buffered, defer: false
        )
        panel.title = "Jarvis Live"
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

struct CopilotPanelView: View {
    @Bindable var model: AppModel

    var body: some View {
        VStack(spacing: 0) {
            header
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    transcript
                    let copilot = model.store.copilot
                    suggestions(copilot?.suggestions ?? [])
                    list("Notes", copilot?.notes.map(\.text) ?? [])
                    actions(copilot?.actions ?? [])
                    list("Decisions", copilot?.decisions.map(\.text) ?? [])
                    related(copilot?.related ?? [])
                    if let final = model.store.finalNote {
                        section("Final note") { Text(final.title).foregroundStyle(JV.accent) }
                    }
                }
                .padding(16)
            }
        }
        .background(JV.bg)
        .foregroundStyle(JV.text)
        .preferredColorScheme(.dark)
        .frame(minWidth: 320, minHeight: 360)
    }

    // MARK: Header

    private var header: some View {
        @Bindable var settings = model.settings
        return HStack(spacing: 8) {
            Circle().fill(model.isRecording ? JV.error : JV.text2.opacity(0.35)).frame(width: 10, height: 10)
            Text(model.isRecording ? "Recording" : "Idle").font(.caption).foregroundStyle(JV.text2)
            Spacer()
            Toggle("On top", isOn: $settings.panelAlwaysOnTop)
                .toggleStyle(.checkbox).font(.caption)
                .onChange(of: model.settings.panelAlwaysOnTop) { model.panel.show() }
        }
        .padding(.horizontal, 16).padding(.top, 28).padding(.bottom, 8)
        .background(JV.surface)
    }

    // MARK: Transcript

    private var transcript: some View {
        section("Transcript") {
            if model.store.lines.isEmpty {
                Text("Nothing yet.").foregroundStyle(JV.text2)
            }
            ForEach(model.store.lines) { line in
                HStack(alignment: .top, spacing: 8) {
                    Rectangle().fill(line.speaker == .me ? JV.accent : JV.violet).frame(width: 3)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(line.speaker == .me ? "Me" : "Them")
                            .font(.caption2.weight(.semibold)).foregroundStyle(JV.text2)
                        // Local-only drafts are never replaced, so they read as normal text.
                        let isGhost = line.isDraft && !model.sessionIsLocalOnly
                        Text(line.text)
                            .italic(isGhost)
                            .foregroundStyle(isGhost ? JV.text2 : JV.text)
                    }
                }
            }
        }
    }

    // MARK: Copilot

    private func suggestions(_ all: [CopilotSuggestion]) -> some View {
        TimelineView(.periodic(from: .now, by: 1)) { _ in
            let now = model.sessionClock?.elapsedMs() ?? 0
            let live = all.filter { !model.dismissedSuggestionIDs.contains($0.id) && $0.expiresAtMs > now }
            if !live.isEmpty {
                section("Suggestions") {
                    ForEach(live) { suggestion in
                        let remaining = suggestion.expiresAtMs - now
                        HStack(alignment: .top) {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(label(for: suggestion.kind)).font(.caption2).foregroundStyle(JV.warning)
                                Text(suggestion.text)
                            }
                            Spacer()
                            Button("\u{2715}") { model.dismissedSuggestionIDs.insert(suggestion.id) }
                                .buttonStyle(.plain).foregroundStyle(JV.text2)
                                .accessibilityLabel("Dismiss suggestion")
                        }
                        .padding(10).background(JV.surface2)
                        .opacity(min(1, max(0.15, Double(remaining) / 5000)))
                        .animation(.easeOut(duration: 0.6), value: remaining < 5000)
                    }
                }
            }
        }
    }

    private func label(for kind: SuggestionKind) -> String {
        switch kind {
        case .question: "QUESTION"
        case .gap: "GAP"
        case .counterpoint: "COUNTERPOINT"
        case .factCheck: "FACT CHECK"
        }
    }

    private func actions(_ items: [CopilotAction]) -> some View {
        Group {
            if !items.isEmpty {
                section("Actions") {
                    ForEach(items) { item in
                        VStack(alignment: .leading, spacing: 2) {
                            Text("\u{25B8} \(item.text)")
                            let meta = [item.owner, item.due].compactMap { $0 }.joined(separator: " \u{00B7} ")
                            if !meta.isEmpty { Text(meta).font(.caption).foregroundStyle(JV.text2) }
                        }
                    }
                }
            }
        }
    }

    private func list(_ title: String, _ items: [String]) -> some View {
        Group {
            if !items.isEmpty {
                section(title) {
                    ForEach(Array(items.enumerated()), id: \.offset) { _, text in Text("\u{2022} \(text)") }
                }
            }
        }
    }

    private func related(_ notes: [RelatedNote]) -> some View {
        Group {
            if !notes.isEmpty {
                section("Related") {
                    ForEach(notes) { note in
                        Button {
                            // Only open Obsidian links; the URI came from the server.
                            if let url = URL(string: note.uri), url.scheme == "obsidian" {
                                NSWorkspace.shared.open(url)
                            }
                        } label: {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(note.title).foregroundStyle(JV.accent)
                                Text(note.snippet).font(.caption).foregroundStyle(JV.text2).lineLimit(2)
                            }
                            .frame(maxWidth: .infinity, alignment: .leading)
                        }
                        .buttonStyle(.plain)
                    }
                }
            }
        }
    }

    private func section<Content: View>(_ title: String, @ViewBuilder _ content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title.uppercased()).font(.caption2.weight(.semibold)).tracking(1.2).foregroundStyle(JV.text2)
            content()
        }
    }
}
