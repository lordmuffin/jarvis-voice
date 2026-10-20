import Foundation
import JarvisLiveKit

/// Builds the text that is copied and pasted from a dictation's final segments.
enum DictationText {
    /// Final segments in time order, joined by single spaces, with no leading or trailing whitespace.
    static func join(_ segments: [DraftSegment]) -> String {
        segments
            .filter(\.final)
            .enumerated()
            .sorted { ($0.element.startMs, $0.offset) < ($1.element.startMs, $1.offset) }
            .flatMap { $0.element.text.split(whereSeparator: \.isWhitespace) }
            .joined(separator: " ")
    }

    /// The tail of the live text that fits in the dictation pill.
    static func tail(_ text: String, maxLength: Int = 80) -> String {
        guard text.count > maxLength else { return text }
        return "\u{2026}" + String(text.suffix(maxLength - 1).drop(while: { $0 == " " }))
    }
}
