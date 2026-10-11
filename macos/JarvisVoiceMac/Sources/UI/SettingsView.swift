import JarvisLiveKit
import SwiftUI

struct SettingsView: View {
    @Bindable var model: AppModel
    @State private var devices: [AudioInputDevice] = []

    var body: some View {
        @Bindable var settings = model.settings
        Form {
            Section("Server") {
                TextField("Server URL", text: $settings.serverURL)
                    .textContentType(.URL)
                SecureField("Device token", text: $settings.deviceToken)
                if settings.serverBaseURL == nil {
                    Text("Enter an http(s) URL.").font(.caption).foregroundStyle(.red)
                }
                Text("The token is stored in the Keychain.").font(.caption).foregroundStyle(.secondary)
            }
            Section("Away from home") {
                Toggle("Local only away from home", isOn: Binding(
                    get: { settings.awayMode.enabled },
                    set: { settings.awayMode = settings.awayMode.withEnabled($0) }
                ))
                Text("Records on this Mac when the server can\u{2019}t be reached and NetBird is off. Flip Local only in the menu to override until you next leave or get home.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Section("Input") {
                Picker("Microphone", selection: $settings.inputDeviceUID) {
                    Text("System default").tag(String?.none)
                    ForEach(devices) { Text($0.name).tag(String?.some($0.uid)) }
                }
                Button("Refresh devices") { devices = AudioDevices.inputDevices() }
            }
            Section("Panel") {
                Toggle("Keep copilot panel on top", isOn: $settings.panelAlwaysOnTop)
            }
            Section("Dictation") {
                Picker("Key", selection: $settings.dictationKey) {
                    ForEach(TriggerKey.allCases) { Text($0.title).tag($0) }
                }
                Picker("Mode", selection: $settings.dictationMode) {
                    ForEach(TriggerMode.allCases) { Text($0.title).tag($0) }
                }
                .disabled(settings.dictationKey == .off)
                Toggle("Paste at the cursor (always copied to the clipboard)", isOn: $settings.dictationPaste)
                    .disabled(settings.dictationKey == .off)
                if settings.dictationKey != .off {
                    if model.dictation.accessibilityTrusted {
                        Text("Accessibility: allowed").font(.caption).foregroundStyle(.secondary)
                    } else {
                        HStack {
                            Text("Allow Jarvis Voice under Accessibility to use the key and paste.")
                                .font(.caption).foregroundStyle(.orange)
                            Spacer()
                            Button("Open Accessibility Settings\u{2026}") { TextInserter.openAccessibilitySettings() }
                        }
                    }
                }
                Text("Runs entirely on this Mac; nothing is sent to the server. Pressing the key together with another key (\u{2318}C, \u{2318}-click) never starts dictation.")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
        .frame(width: 460)
        .padding(.vertical, 8)
        .onAppear {
            devices = AudioDevices.inputDevices()
            model.dictation.refreshTrust()
        }
        .onChange(of: settings.dictationKey) { model.dictation.startMonitoring() }
        .onChange(of: settings.serverURL) { model.recheckNetwork() }
        .onChange(of: settings.deviceToken) { model.recheckNetwork() }
        .onChange(of: settings.awayMode.enabled) { model.recheckNetwork() }
        .onChange(of: settings.dictationMode) { model.dictation.startMonitoring() }
    }
}
