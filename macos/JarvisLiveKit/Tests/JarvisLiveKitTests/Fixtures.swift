import Foundation

/// Locates the shared protocol conformance fixtures relative to this source file.
enum Fixtures {
    /// `<repo>/protocol/v1/fixtures`
    static let root: URL = {
        var url = URL(filePath: #filePath)
        for _ in 0..<5 { url.deleteLastPathComponent() }  // file → tests dir → Tests → package → macos → repo
        return url.appending(path: "protocol/v1/fixtures", directoryHint: .isDirectory)
    }()

    static func jsonFiles(in subdirectory: String) -> [URL] {
        let dir = root.appending(path: subdirectory, directoryHint: .isDirectory)
        let urls = (try? FileManager.default.contentsOfDirectory(at: dir, includingPropertiesForKeys: nil)) ?? []
        return urls.filter { $0.pathExtension == "json" }.sorted { $0.lastPathComponent < $1.lastPathComponent }
    }

    static var valid: [URL] { jsonFiles(in: "valid") }
    static var invalid: [URL] { jsonFiles(in: "invalid") }

    /// `ack__string_seq.json` → `ack`
    static func schemaName(of url: URL) -> String {
        let stem = url.deletingPathExtension().lastPathComponent
        return stem.components(separatedBy: "__").first ?? stem
    }
}
