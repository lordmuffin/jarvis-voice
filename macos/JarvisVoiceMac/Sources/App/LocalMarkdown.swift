import Foundation
import JarvisLiveKit

/// Rendering and parsing for the local-only session files in `~/Documents/Jarvis Live/`.
enum LocalMarkdown {
    struct Header: Equatable {
        var sessionId: String
        var mode: SessionMode
        var channels: [Channel]
        var started: Date
        var uploadedAs: String?
        /// Recorded here because the server was down: upload it by itself once the server is back.
        var autoUpload = false
        /// The server session an upload is sending this recording to, written before any audio moves
        /// so an interrupted upload resumes it. Nil until an upload has started.
        var serverSessionId: String?
    }

    struct Line: Equatable {
        var channel: Channel
        var startMs: Int
        var text: String
    }

    static var directory: URL {
        URL.documentsDirectory.appending(path: "Jarvis Live", directoryHint: .isDirectory)
    }

    static func render(header: Header, lines: [Line], markers: [(tMs: Int, label: String)]) -> String {
        let iso = ISO8601DateFormatter().string(from: header.started)
        var out = """
        ---
        jarvis_session: \(header.sessionId)
        mode: \(header.mode.rawValue)
        channels: \(header.channels.map(\.rawValue).joined(separator: ","))
        started: \(iso)
        uploaded: \(header.uploadedAs ?? "false")
        \(header.autoUpload ? "upload: auto\n" : "")\(header.serverSessionId.map { "server_session: \($0)\n" } ?? "")---

        # Jarvis Live \u{2014} \(title(for: header.started))

        """
        if !markers.isEmpty {
            out += "\n## Markers\n\n"
            for marker in markers.sorted(by: { $0.tMs < $1.tMs }) {
                out += "- `\(clock(marker.tMs))` \(marker.label)\n"
            }
        }
        out += "\n## Transcript (on-device draft)\n\n"
        for line in lines.sorted(by: { ($0.startMs, $0.channel.wireIndex) < ($1.startMs, $1.channel.wireIndex) }) {
            let speaker = line.channel == .mic ? "Me" : "Them"
            out += "**\(speaker)** `\(clock(line.startMs))` \(line.text)\n\n"
        }
        return out
    }

    static func parseHeader(_ text: String) -> Header? {
        guard text.hasPrefix("---\n") else { return nil }
        let body = text.dropFirst(4)
        guard let end = body.range(of: "\n---") else { return nil }
        var fields: [String: String] = [:]
        for line in body[..<end.lowerBound].split(separator: "\n") {
            guard let colon = line.firstIndex(of: ":") else { continue }
            fields[String(line[..<colon])] = line[line.index(after: colon)...].trimmingCharacters(in: .whitespaces)
        }
        guard let id = fields["jarvis_session"], let modeRaw = fields["mode"],
              let mode = SessionMode(rawValue: modeRaw), let startedRaw = fields["started"],
              let started = ISO8601DateFormatter().date(from: startedRaw) else { return nil }
        let channels = (fields["channels"] ?? "mic").split(separator: ",").compactMap { Channel(rawValue: String($0)) }
        let uploaded = fields["uploaded"].flatMap { $0 == "false" ? nil : $0 }
        return Header(
            sessionId: id, mode: mode, channels: channels, started: started, uploadedAs: uploaded,
            autoUpload: fields["upload"] == "auto", serverSessionId: fields["server_session"]
        )
    }

    /// Records the server session an upload targets; a no-op if it is already recorded.
    static func settingServerSession(_ text: String, id: String) -> String {
        guard parseHeader(text)?.serverSessionId == nil else { return text }
        return text.replacingOccurrences(of: "\nuploaded: false\n", with: "\nuploaded: false\nserver_session: \(id)\n")
    }

    static func markUploaded(_ text: String, serverSessionId: String) -> String {
        text.replacingOccurrences(of: "\nuploaded: false\n", with: "\nuploaded: \(serverSessionId)\n")
    }

    static func clock(_ ms: Int) -> String {
        let total = max(0, ms) / 1000
        return String(format: "%02d:%02d", total / 60, total % 60)
    }

    static func fileName(for started: Date) -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyy-MM-dd HHmm"
        return "\(formatter.string(from: started)) Jarvis Live.md"
    }

    private static func title(for date: Date) -> String {
        date.formatted(date: .abbreviated, time: .shortened)
    }
}
