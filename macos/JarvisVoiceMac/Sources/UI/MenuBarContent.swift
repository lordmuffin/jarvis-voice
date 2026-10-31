import AppKit
import JarvisLiveKit
import SwiftUI

struct MenuBarContent: View {
    @Bindable var model: AppModel
    @Environment(\.openSettings) private var openSettings

    var body: some View {
        // A `.menu` MenuBarExtra renders only text and images, so the channel pill is its tag in the title.
        Text(MenuText.header(version: AppInfo.version, channel: model.serverChannel))
        if let serverText {
            Text(serverText)
        }
        Text(MenuText.status(link: model.link, isRecording: model.isRecording))
        Text("STT: \(sttText)")
        if let progress = model.assetProgress {
            Text("Installing speech model \(Int(progress * 100))%")
        }

        Divider()

        Button(model.isRecording ? "Stop Session" : "Start Session") {
            Task { await model.toggle() }
        }
        .keyboardShortcut("j", modifiers: [.control, .option])
        .disabled(model.isBusy)
        .help(model.isBusy ? "Another action is running." : "")
        if model.isBusy { Text("Another action is running.") }

        Picker("Mode", selection: $model.mode) {
            ForEach(AppModel.Mode.allCases) { Text($0.title).tag($0) }
        }
        .disabled(model.phase != .idle)
        .help(model.phase != .idle ? "Available while idle." : "")

        Toggle("Local-only (record here, upload later)", isOn: $model.localOnly)
            .disabled(model.phase != .idle)
            .help(model.phase != .idle ? "Available while idle." : "")
        if model.phase != .idle { Text("Available while idle.") }
        if let networkText {
            Text(networkText)
        }

        Divider()

        if model.isRecording {
            if model.mode == .meeting {
                switch model.systemState {
                case .failed(let why): Text(MenuText.warning("System audio off: \(why)"))
                case .noAudio: Text(MenuText.warning("No system audio. Check Privacy \u{203A} Audio Capture."))
                case .running, .idle: EmptyView()
                }
            }
            if !model.voiceProcessing { Text(MenuText.warning("Echo cancellation unavailable on this input.")) }
        }
        if let notice = model.notice { Text(notice) }
        if model.settings.dictationKey != .off {
            if model.dictation.accessibilityTrusted {
                Text("Dictation: \(dictationHint)")
            } else {
                Text(MenuText.warning("Dictation needs Accessibility (see Settings)."))
            }
        }

        Divider()

        Button("Add Marker") { model.addMarker() }
            .keyboardShortcut("m", modifiers: [.control, .option])
            .disabled(!model.isRecording)
            .help(model.isRecording ? "" : "Start a session first.")
        if !model.isRecording { Text("Start a session first.") }
        Button("Show Copilot Panel") { model.panel.show() }

        let pending = model.localSessions.filter { !$0.isUploaded }
        if !pending.isEmpty {
            Menu("Upload for Full Processing") {
                if model.uploadingSessionId != nil { Text("An upload is already running.") }
                ForEach(pending) { session in
                    Button(session.url.deletingPathExtension().lastPathComponent) {
                        Task { await model.upload(session) }
                    }
                    .disabled(model.uploadingSessionId != nil)
                    .help(model.uploadingSessionId != nil ? "An upload is already running." : "")
                }
            }
        }
        Button("Open Local Transcripts") {
            NSWorkspace.shared.open(LocalMarkdown.directory)
        }

        Divider()

        Button("Settings\u{2026}") { showSettings() }
            .keyboardShortcut(",")
        Button("Quit Jarvis Voice") { NSApp.terminate(nil) }
            .keyboardShortcut("q")
    }

    /// Which server sessions go to: "Server: LAB · live.lab.apj.dev (0.12.0)"; nil without a valid URL.
    private var serverText: String? {
        MenuText.server(channel: model.serverChannel, host: model.settings.serverBaseURL?.host(), version: model.serverVersion)
    }

    /// `showSettingsWindow:` does nothing since macOS 14; `openSettings` is the supported way.
    private func showSettings() {
        openSettings()
        NSApp.activate()
        // A menu-bar app isn't frontmost, so the window can open behind the app in use. Raise it
        // once SwiftUI has created it.
        DispatchQueue.main.async {
            MainActor.assumeIsolated {
                NSApp.windows
                    .first { $0.identifier?.rawValue == "com_apple_SwiftUI_Settings_window" }?
                    .orderFrontRegardless()
            }
        }
    }

    /// Where the Mac is and why Local-only is what it is; nil when the automation is off.
    private var networkText: String? {
        let away = model.settings.awayMode
        guard away.enabled, model.settings.hasCredentials else { return nil }
        let place = switch away.place {
        case .home: "Home network"
        case .netbird: "On NetBird"
        case .away: "Away from home"
        case nil: "Checking the network\u{2026}"
        }
        guard away.place != nil else { return place }
        let why = if away.manual == nil {
            away.localOnly ? "Local-only turned on" : "streaming"
        } else if away.automatic == true {
            "your choice until you\u{2019}re home or on NetBird"
        } else {
            "your choice until you leave home"
        }
        return "\(place) \u{00B7} \(why)"
    }

    private var dictationHint: String {
        let key = model.settings.dictationKey.title
        switch model.settings.dictationMode {
        case .hold: return "hold \(key)"
        case .tap: return "tap \(key)"
        case .doubleTap: return "double-tap \(key)"
        }
    }

    private var sttText: String {
        if let tier = model.store.sttTier, model.link == .connected { return "server \(tier)" }
        return model.isRecording ? "on-device draft" : "\u{2014}"
    }
}
