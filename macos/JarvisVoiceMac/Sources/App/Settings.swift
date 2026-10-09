import Foundation
import Observation
import Security

/// Persistent user settings. The device token lives in the Keychain; everything else in `UserDefaults`.
@MainActor
@Observable
final class SettingsModel {
    private static let urlKey = "serverURL"
    private static let deviceKey = "inputDeviceUID"
    private static let onTopKey = "panelAlwaysOnTop"
    private static let defaultServer = "https://live.lab.apj.dev"

    var serverURL: String {
        didSet { UserDefaults.standard.set(serverURL, forKey: Self.urlKey) }
    }
    /// `nil` means the system default input.
    var inputDeviceUID: String? {
        didSet { UserDefaults.standard.set(inputDeviceUID, forKey: Self.deviceKey) }
    }
    var panelAlwaysOnTop: Bool {
        didSet { UserDefaults.standard.set(panelAlwaysOnTop, forKey: Self.onTopKey) }
    }
    var deviceToken: String {
        didSet { Keychain.set(deviceToken, account: "device-token") }
    }

    init() {
        let defaults = UserDefaults.standard
        serverURL = defaults.string(forKey: Self.urlKey) ?? Self.defaultServer
        inputDeviceUID = defaults.string(forKey: Self.deviceKey)
        panelAlwaysOnTop = defaults.object(forKey: Self.onTopKey) as? Bool ?? true
        deviceToken = Keychain.get(account: "device-token") ?? ""
    }

    var serverBaseURL: URL? {
        guard let url = URL(string: serverURL.trimmingCharacters(in: .whitespaces)),
              let scheme = url.scheme, ["http", "https"].contains(scheme), url.host != nil else { return nil }
        return url
    }

    var hasCredentials: Bool { serverBaseURL != nil && !deviceToken.isEmpty }
}

enum Keychain {
    private static let service = "dev.apj.JarvisVoiceMac"

    static func get(account: String) -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var item: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &item) == errSecSuccess,
              let data = item as? Data else { return nil }
        return String(decoding: data, as: UTF8.self)
    }

    static func set(_ value: String, account: String) {
        let base: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        SecItemDelete(base as CFDictionary)
        guard !value.isEmpty else { return }
        var add = base
        add[kSecValueData as String] = Data(value.utf8)
        add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        SecItemAdd(add as CFDictionary, nil)
    }
}
