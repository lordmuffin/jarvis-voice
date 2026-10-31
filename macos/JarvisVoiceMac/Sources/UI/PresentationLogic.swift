import Foundation
import JarvisLiveKit

// Pure presentation rules for the menu-bar item, the Copilot panel, the menu and the HUD. They hold
// no views so they can be unit tested; the views only draw what these return.

/// `18:42`, or `1:02:03` past an hour.
enum ClockFormat {
    static func elapsed(seconds: Int) -> String {
        let s = max(0, seconds)
        let h = s / 3600, m = (s % 3600) / 60, sec = s % 60
        if h > 0 { return String(format: "%d:%02d:%02d", h, m, sec) }
        return String(format: "%02d:%02d", m, sec)
    }

    /// A transcript timestamp from milliseconds into the session: `18:42`.
    static func stamp(ms: Int) -> String { elapsed(seconds: ms / 1000) }
}

/// What the menu-bar item shows. Every state is monochrome: shape carries the state and the channel.
struct MenuBarIconSpec: Equatable {
    enum Glyph: Equatable {
        case waveform
        case recording(elapsed: String)
        case dictating
    }

    var glyph: Glyph
    /// `LAB`, `LIVE`, …; nil when no server address is set.
    var badge: String?
    /// LAB is a filled pill, LIVE (and nothing else) outlined, so they differ by shape.
    var badgeFilled: Bool
    var accessibilityLabel: String

    /// A recording session wins over dictation. A local session shows `LOCAL` instead of the server's
    /// channel: it isn't talking to that server, and a LIVE badge on it would be a lie.
    static func make(
        isRecording: Bool, isDictating: Bool, elapsedSeconds: Int, channel: ServerChannel?, localOnly: Bool = false
    ) -> Self {
        let glyph: Glyph
        let state: String
        if isRecording {
            let elapsed = ClockFormat.elapsed(seconds: elapsedSeconds)
            glyph = .recording(elapsed: elapsed)
            state = localOnly ? "recording \(elapsed), local only, not streaming" : "recording \(elapsed)"
        } else if isDictating {
            glyph = .dictating
            state = "dictating"
        } else {
            glyph = .waveform
            state = "idle"
        }
        if isRecording && localOnly {
            return Self(
                glyph: glyph, badge: "LOCAL", badgeFilled: true,
                accessibilityLabel: ["Jarvis", state].joined(separator: ", ")
            )
        }
        let badge = channel?.tag
        let label = ["Jarvis", state, badge].compactMap { $0 }.joined(separator: ", ")
        return Self(glyph: glyph, badge: badge, badgeFilled: channel != .live, accessibilityLabel: label)
    }
}

/// Suggestions fade over their last seconds and are gone at `expiresAtMs`.
enum SuggestionAgeing {
    static let fadeWindowMs = 5000
    static let floorOpacity = 0.15

    static func opacity(remainingMs: Int) -> Double {
        min(1, max(floorOpacity, Double(remainingMs) / Double(fadeWindowMs)))
    }

    static func isAgeing(remainingMs: Int) -> Bool { remainingMs < fadeWindowMs }

    /// Live suggestions: not dismissed, not expired.
    static func live(
        _ all: [CopilotSuggestion], dismissed: Set<String>, nowMs: Int
    ) -> [CopilotSuggestion] {
        all.filter { !dismissed.contains($0.id) && $0.expiresAtMs > nowMs }
    }

    /// The suggestion to show in the "Now" dock (the one that expires last, so the newest) and the rest.
    static func split(_ live: [CopilotSuggestion]) -> (now: CopilotSuggestion?, waiting: [CopilotSuggestion]) {
        guard let newest = live.max(by: { $0.expiresAtMs < $1.expiresAtMs }) else { return (nil, []) }
        return (newest, live.filter { $0.id != newest.id })
    }
}

/// The three panel layouts, picked from the panel's width.
enum PanelLayout: Equatable {
    case strip, standard, twoColumn

    static func forWidth(_ width: CGFloat) -> Self {
        if width < 400 { return .strip }
        if width < 620 { return .standard }
        return .twoColumn
    }
}

/// Which feed rows the person hasn't looked at yet. A "glance" is the pointer entering or leaving the
/// panel; rows that arrived since get a New mark and a separator above the first of them.
struct UnseenTracker: Equatable {
    private(set) var seen: Set<String> = []
    private(set) var glancedAt: Date?

    mutating func glance(ids: [String], at date: Date = .now) {
        seen = Set(ids)
        glancedAt = date
    }

    /// Before the first glance nothing is new, so a panel that just opened doesn't mark everything.
    func unseen(in ids: [String]) -> [String] {
        guard glancedAt != nil else { return [] }
        return ids.filter { !seen.contains($0) }
    }

    func separatorIndex(in ids: [String]) -> Int? {
        guard glancedAt != nil else { return nil }
        return ids.firstIndex { !seen.contains($0) }
    }
}

/// A row of the feed: speech or a marker, in time order.
enum FeedRow: Identifiable, Equatable {
    case speech(TranscriptLine)
    case marker(id: String, tMs: Int, label: String)

    var id: String {
        switch self {
        case .speech(let line): line.id
        case .marker(let id, _, _): id
        }
    }

    var startMs: Int {
        switch self {
        case .speech(let line): line.startMs
        case .marker(_, let tMs, _): tMs
        }
    }
}

enum FeedBuilder {
    /// Lines and markers merged by time; a marker sorts after speech that started at the same moment.
    static func rows(lines: [TranscriptLine], markers: [(tMs: Int, label: String)]) -> [FeedRow] {
        let markerRows = markers.enumerated().map { FeedRow.marker(id: "marker-\($0.offset)", tMs: $0.element.tMs, label: $0.element.label) }
        let all = lines.map(FeedRow.speech) + markerRows
        return all.enumerated()
            .sorted { ($0.element.startMs, $0.offset) < ($1.element.startMs, $1.offset) }
            .map(\.element)
    }
}

/// The by-kind sections, in the order every Jarvis app shows them.
enum CopilotKind: String, CaseIterable, Identifiable {
    case actions = "Actions"
    case decisions = "Decisions"
    case notes = "Notes"
    case related = "Related"

    var id: String { rawValue }

    func count(in copilot: CopilotSnapshot?) -> Int {
        guard let copilot else { return 0 }
        switch self {
        case .actions: return copilot.actions.count
        case .decisions: return copilot.decisions.count
        case .notes: return copilot.notes.count
        case .related: return copilot.related.count
        }
    }
}

/// Where a Related link may point: only Obsidian, since the URI came from the server.
enum RelatedLink {
    static func url(_ uri: String) -> URL? {
        guard let url = URL(string: uri), url.scheme == "obsidian" else { return nil }
        return url
    }
}

/// What the dictation pill shows.
struct HUDContent: Equatable {
    enum Tone: Equatable { case active, quiet, done, error, notice }

    var label: String
    var tone: Tone
    /// The last words, when there are any to show.
    var tail: String?
    var meter: Bool
    var spinner: Bool

    static func make(
        state: DictationController.State, message: String?, liveText: String, assetProgress: Double?
    ) -> Self {
        if let message { return fromMessage(message) }
        if state != .idle, let progress = assetProgress {
            return Self(label: "Installing speech model \(Int(progress * 100))%", tone: .quiet, tail: nil, meter: false, spinner: true)
        }
        switch state {
        case .finishing:
            return Self(label: "TRANSCRIBING", tone: .quiet, tail: nil, meter: false, spinner: true)
        case .listening where !liveText.isEmpty:
            return Self(label: "LISTENING", tone: .active, tail: DictationText.tail(liveText), meter: true, spinner: false)
        case .listening:
            return Self(label: "LISTENING", tone: .active, tail: nil, meter: true, spinner: false)
        case .idle:
            return Self(label: "", tone: .quiet, tail: nil, meter: false, spinner: false)
        }
    }

    private static func fromMessage(_ message: String) -> Self {
        let plain = message.replacingOccurrences(of: "\u{2019}", with: "'")
        switch plain {
        case "Copied":
            return Self(label: "\u{2713} COPIED", tone: .done, tail: nil, meter: false, spinner: false)
        case "Didn't catch that":
            return Self(label: "\u{2715} DIDN'T CATCH THAT", tone: .error, tail: nil, meter: false, spinner: false)
        default:
            return Self(label: "\u{2691}", tone: .notice, tail: message, meter: false, spinner: false)
        }
    }
}

/// Menu strings that depend only on values, kept out of the view.
enum MenuText {
    static func status(link: AppModel.Link, isRecording: Bool) -> String {
        switch link {
        case .connected: "\u{25CF} Live"
        case .reconnecting: "\u{25D0} Reconnecting\u{2026}"
        case .offlineLocal: "\u{25CB} Local-only"
        case .failed(let why): "\u{2715} Degraded: \(why)"
        case .idle: isRecording ? "\u{25CF} Recording" : "\u{25CB} Idle"
        }
    }

    /// The menu title with the server's channel tag, the text form of the badge shown in the panel and Settings.
    static func header(version: String, channel: ServerChannel?) -> String {
        guard let channel else { return "Jarvis Voice \(version)" }
        return "Jarvis Voice \(version) \u{00B7} \(channel.tag)"
    }

    static func server(channel: ServerChannel?, host: String?, version: String?) -> String? {
        guard let channel, let host else { return nil }
        let suffix = version.map { " (\($0))" } ?? ""
        return "Server: \(channel.tag) \u{00B7} \(host)\(suffix)"
    }

    static func warning(_ text: String) -> String { "\u{2691} \(text)" }
}
