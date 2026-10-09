import AppKit
import SwiftUI

struct MenuBarContent: View {
    @Bindable var model: AppModel

    var body: some View {
        Button(model.isRecording ? "Stop Session" : "Start Session") {
            Task { await model.toggle() }
        }
        .keyboardShortcut("j", modifiers: [.control, .option])
        .disabled(model.isBusy)

        Picker("Mode", selection: $model.mode) {
            ForEach(AppModel.Mode.allCases) { Text($0.title).tag($0) }
        }
        .disabled(model.phase != .idle)

        Toggle("Local only (no network)", isOn: $model.localOnly)
            .disabled(model.phase != .idle)

        Divider()

        Text("\(statusGlyph) \(statusText)")
        Text("STT: \(sttText)")
        if let progress = model.assetProgress {
            Text("Installing speech model \(Int(progress * 100))%")
        }
        if model.isRecording {
            if model.mode == .meeting {
                switch model.systemState {
                case .failed(let why): Text("\u{26A0} System audio off: \(why)")
                case .noAudio: Text("\u{26A0} No system audio. Check Privacy \u{203A} Audio Capture.")
                case .running, .idle: EmptyView()
                }
            }
            if !model.voiceProcessing { Text("\u{26A0} Echo cancellation unavailable on this input") }
        }
        if let notice = model.notice { Text(notice) }

        Divider()

        Button("Add Marker") { model.addMarker() }
            .keyboardShortcut("m", modifiers: [.control, .option])
            .disabled(!model.isRecording)
        Button("Show Copilot Panel") { model.panel.show() }

        let pending = model.localSessions.filter { !$0.isUploaded }
        if !pending.isEmpty {
            Menu("Upload for full processing") {
                ForEach(pending) { session in
                    Button(session.url.deletingPathExtension().lastPathComponent) {
                        Task { await model.upload(session) }
                    }
                    .disabled(model.uploadingSessionId != nil)
                }
            }
        }
        Button("Open Local Transcripts") {
            NSWorkspace.shared.open(LocalMarkdown.directory)
        }

        Divider()

        Button("Settings\u{2026}") {
            NSApp.activate(ignoringOtherApps: true)
            NSApp.sendAction(Selector(("showSettingsWindow:")), to: nil, from: nil)
        }
        .keyboardShortcut(",")
        Button("Quit Jarvis Voice") { NSApp.terminate(nil) }
            .keyboardShortcut("q")
    }

    private var statusGlyph: String {
        switch model.link {
        case .connected: "\u{1F7E2}"
        case .reconnecting: "\u{1F7E0}"
        case .offlineLocal: "\u{1F535}"
        case .failed: "\u{1F534}"
        case .idle: "\u{26AA}\u{FE0F}"
        }
    }

    private var statusText: String {
        switch model.link {
        case .connected: "Connected"
        case .reconnecting: "Reconnecting\u{2026}"
        case .offlineLocal: "Offline (local only)"
        case .failed(let why): "Failed: \(why)"
        case .idle: model.isRecording ? "Recording" : "Idle"
        }
    }

    private var sttText: String {
        if let tier = model.store.sttTier, model.link == .connected { return "server \(tier)" }
        return model.isRecording ? "on-device draft" : "\u{2014}"
    }
}
