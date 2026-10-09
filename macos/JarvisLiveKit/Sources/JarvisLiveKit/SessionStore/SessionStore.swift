import Foundation
import Observation

/// One row of the live transcript.
public struct TranscriptLine: Identifiable, Equatable, Sendable {
    public var id: String
    public var channel: Channel
    public var speaker: Speaker
    public var startMs: Int
    public var endMs: Int
    public var text: String
    /// `true` for client-side drafts; `false` once the server's authoritative segment arrived.
    public var isDraft: Bool
    public var sttTier: String?

    public init(
        id: String, channel: Channel, speaker: Speaker, startMs: Int, endMs: Int,
        text: String, isDraft: Bool, sttTier: String? = nil
    ) {
        self.id = id
        self.channel = channel
        self.speaker = speaker
        self.startMs = startMs
        self.endMs = endMs
        self.text = text
        self.isDraft = isDraft
        self.sttTier = sttTier
    }

    /// Whether two lines on the same channel cover overlapping time.
    func overlaps(_ other: TranscriptLine) -> Bool {
        guard channel == other.channel else { return false }
        if startMs == other.startMs && endMs == other.endMs { return true }
        return startMs < other.endMs && other.startMs < endMs
    }
}

/// UI-facing state for one live session. All mutation happens on the main actor.
@MainActor
@Observable
public final class SessionStore {
    /// Ordered by start time. Drafts are replaced by final segments that overlap them.
    public private(set) var lines: [TranscriptLine] = []
    /// The newest full-state copilot snapshot (never merged).
    public private(set) var copilot: CopilotSnapshot?
    public private(set) var connection: ConnectionState = .idle
    /// Active speech-to-text tier as last reported by `status`; `nil` when none.
    public private(set) var sttTier: String?
    public private(set) var lagMs: Int = 0
    public private(set) var llmOk: Bool = true
    public private(set) var finalNote: FinalNote?
    public private(set) var lastError: ServerError?

    @ObservationIgnored private var draftCounter = 0
    @ObservationIgnored private var consumers: [Task<Void, Never>] = []

    public init() {}

    // MARK: - Input

    /// Applies a server event.
    public func apply(_ event: ServerEvent) {
        switch event {
        case .segment(let segment):
            applyFinal(segment)
        case .copilot(let snapshot):
            if copilot.map({ snapshot.version > $0.version }) ?? true { copilot = snapshot }
        case .status(let status):
            sttTier = status.sttTier
            lagMs = status.lagMs
            llmOk = status.llmOk
        case .finalNote(let note):
            finalNote = note
        case .error(let error):
            lastError = error
        case .ack, .helloAck:
            break  // Handled by the transport.
        }
    }

    /// Shows a locally produced draft immediately. It is replaced as soon as the server confirms
    /// the same stretch of audio.
    public func applyDraft(_ draft: DraftSegment) {
        draftCounter += 1
        let line = TranscriptLine(
            id: "draft-\(draftCounter)",
            channel: draft.channel,
            speaker: draft.channel == .mic ? .me : .them,
            startMs: draft.startMs,
            endMs: draft.endMs,
            text: draft.text,
            isDraft: true
        )
        // A draft that arrives after the server already settled that time span is stale.
        if lines.contains(where: { !$0.isDraft && $0.overlaps(line) }) { return }
        // A newer draft supersedes earlier drafts of the same utterance.
        lines.removeAll { $0.isDraft && $0.overlaps(line) }
        insert(line)
    }

    public func setConnection(_ state: ConnectionState) {
        connection = state
    }

    /// Streams `connection`'s events and state changes into this store until `detach()`.
    public func attach(_ connection: LiveConnection) {
        detach()
        let events = connection.events
        let states = connection.states
        consumers = [
            Task { [weak self] in
                for await event in events { self?.apply(event) }
            },
            Task { [weak self] in
                for await state in states { self?.setConnection(state) }
            },
        ]
    }

    public func detach() {
        consumers.forEach { $0.cancel() }
        consumers = []
    }

    // MARK: - Internals

    private func applyFinal(_ segment: Segment) {
        let line = TranscriptLine(
            id: segment.id,
            channel: segment.channel,
            speaker: segment.speaker,
            startMs: segment.startMs,
            endMs: segment.endMs,
            text: segment.text,
            isDraft: false,
            sttTier: segment.sttTier
        )
        lines.removeAll { $0.id == line.id || ($0.isDraft && $0.overlaps(line)) }
        insert(line)
    }

    /// Inserts keeping lines ordered by start, then end, then channel (mic first), then id, so equal
    /// timestamps never reorder between redraws.
    private func insert(_ line: TranscriptLine) {
        func key(_ l: TranscriptLine) -> (Int, Int, UInt8, String) {
            (l.startMs, l.endMs, l.channel.wireIndex, l.id)
        }
        let index = lines.firstIndex { key($0) > key(line) } ?? lines.endIndex
        lines.insert(line, at: index)
    }
}
