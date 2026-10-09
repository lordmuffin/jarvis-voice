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
        }
        .formStyle(.grouped)
        .frame(width: 460)
        .padding(.vertical, 8)
        .onAppear { devices = AudioDevices.inputDevices() }
    }
}
