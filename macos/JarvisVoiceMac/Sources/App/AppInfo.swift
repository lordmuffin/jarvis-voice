import Foundation

enum AppInfo {
    /// `CFBundleShortVersionString`, which the Makefile sets from `macos/version.txt`.
    static var version: String {
        Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "unknown"
    }
}
