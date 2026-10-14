import JarvisLiveKit
import SwiftUI

struct SettingsView: View {
    @Bindable var model: AppModel
    @State private var devices: [AudioInputDevice] = []

    var body: some View {
        @Bindable var settings = model.settings
        VStack(spacing: 0) {
            titleStrip
            ScrollView {
                HStack(alignment: .top, spacing: InstSpace.s16) {
                    VStack(alignment: .leading, spacing: InstSpace.s24) {
                        serverSection(settings: $settings)
                        awaySection(settings: $settings)
                        inputSection(settings: $settings)
                        panelSection(settings: $settings)
                    }
                    .frame(maxWidth: .infinity, alignment: .topLeading)
                    VStack(alignment: .leading, spacing: InstSpace.s24) {
                        dictationSection(settings: $settings)
                    }
                    .frame(maxWidth: .infinity, alignment: .topLeading)
                }
                .padding(InstSpace.s16)
            }
        }
        .frame(width: 836, height: 640)
        .background(Color.inst.bg)
        .foregroundStyle(Color.inst.text)
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

    // MARK: Title strip

    private var titleStrip: some View {
        HStack(spacing: 8) {
            Text("Settings").instStyle(InstType.caption).fontWeight(.bold)
            if let channel = model.serverChannel { InstBadge(channel: channel) }
            Spacer()
            Text("Jarvis Voice \(AppInfo.version)")
                .font(InstType.meta.font).fontDesign(.monospaced).foregroundStyle(Color.inst.text2)
        }
        .padding(.horizontal, InstSpace.s12)
        .frame(height: 32)
        .background(Color.inst.surface)
        .overlay(alignment: .bottom) { Rectangle().fill(Color.inst.line).frame(height: 1) }
    }

    // MARK: Sections

    private func serverSection(settings: Bindable<SettingsModel>) -> some View {
        section("Server") {
            field("Server URL") {
                TextField("", text: settings.serverURL)
                    .textContentType(.URL)
                    .modifier(InstFieldStyle())
                    .accessibilityLabel("Server URL")
            }
            if model.settings.serverBaseURL == nil {
                InstHelp("Enter an http(s) URL.", warning: true)
            } else {
                HStack(spacing: 6) {
                    InstHelp("Channel")
                    if let channel = model.serverChannel { InstBadge(channel: channel) }
                    Text(serverVersionText)
                        .font(InstType.meta.font).fontDesign(.monospaced).foregroundStyle(Color.inst.text2)
                }
            }
            field("Device token") {
                SecureField("", text: settings.deviceToken)
                    .modifier(InstFieldStyle())
                    .accessibilityLabel("Device token")
                InstHelp("The token is stored in the Keychain.")
            }
        }
    }

    private func awaySection(settings: Bindable<SettingsModel>) -> some View {
        section("Away from home") {
            Toggle("Local only away from home", isOn: Binding(
                get: { settings.wrappedValue.awayMode.enabled },
                set: { settings.wrappedValue.awayMode = settings.wrappedValue.awayMode.withEnabled($0) }
            ))
            .toggleStyle(InstSwitchStyle())
            InstHelp("Records on this Mac when the server can\u{2019}t be reached and NetBird is off. Flip Local only in the menu to override until you next leave or get home.")
        }
    }

    private func inputSection(settings: Bindable<SettingsModel>) -> some View {
        section("Input") {
            field("Microphone") {
                Picker("Microphone", selection: settings.inputDeviceUID) {
                    Text("System default").tag(String?.none)
                    ForEach(devices) { Text($0.name).tag(String?.some($0.uid)) }
                }
                .labelsHidden()
                .pickerStyle(.menu)
                Button("Refresh devices") { devices = AudioDevices.inputDevices() }
                    .buttonStyle(InstButtonStyle())
            }
        }
    }

    private func panelSection(settings: Bindable<SettingsModel>) -> some View {
        section("Panel") {
            Toggle("Keep copilot panel on top", isOn: settings.panelAlwaysOnTop)
                .toggleStyle(InstSwitchStyle())
            InstHelp("The panel floats over other windows and never takes focus.")
        }
    }

    private func dictationSection(settings: Bindable<SettingsModel>) -> some View {
        let off = settings.wrappedValue.dictationKey == .off
        return section("Dictation") {
            field("Key") {
                Picker("Key", selection: settings.dictationKey) {
                    ForEach(TriggerKey.allCases) { Text($0.title).tag($0) }
                }
                .labelsHidden()
                .pickerStyle(.menu)
            }
            field("Mode") {
                InstSegmented(options: TriggerMode.allCases, selection: settings.dictationMode, title: { $0.title })
                    .disabled(off)
                    .opacity(off ? 0.5 : 1)
            }
            Toggle("Paste at the cursor (always copied to the clipboard)", isOn: settings.dictationPaste)
                .toggleStyle(InstSwitchStyle())
                .disabled(off)
                .opacity(off ? 0.5 : 1)
            if !off {
                if model.dictation.accessibilityTrusted {
                    InstHelp("Accessibility: allowed")
                } else {
                    InstHelp("Allow Jarvis Voice under Accessibility to use the key and paste.", warning: true)
                    Button("Open Accessibility Settings\u{2026}") { TextInserter.openAccessibilitySettings() }
                        .buttonStyle(InstButtonStyle())
                }
            }
            InstHelp("Runs entirely on this Mac; nothing is sent to the server. Pressing the key together with another key (\u{2318}C, \u{2318}-click) never starts dictation.")
        }
    }

    // MARK: Pieces

    private func section<Content: View>(_ title: String, @ViewBuilder _ content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            InstSectionLabel(title: title)
            InstGroup { content() }
        }
    }

    private func field<Content: View>(_ label: String, @ViewBuilder _ content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label).instStyle(InstType.caption).fontWeight(.semibold)
            content()
        }
    }

    private var serverVersionText: String {
        if model.settings.serverBaseURL == nil { return "No server URL" }
        return model.serverVersion.map { "v\($0)" } ?? "Version not reported yet"
    }
}

/// A text field in the Instrument style: mono text on `surface2` with a `control` outline.
private struct InstFieldStyle: ViewModifier {
    func body(content: Content) -> some View {
        content
            .textFieldStyle(.plain)
            .font(InstType.body.font.monospaced())
            .padding(.horizontal, 8)
            .frame(height: InstSize.controlMd)
            .background(Color.inst.surface2)
            .overlay(Rectangle().stroke(Color.inst.control, lineWidth: 1))
    }
}
