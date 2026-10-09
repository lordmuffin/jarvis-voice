import Foundation

/// `hello_ack` — last seq the server has stored per channel.
public struct HelloAck: Equatable, Sendable, Codable, TaggedMessage {
    public static let typeTag = "hello_ack"

    public var session: String
    public var acked: ChannelSeqs

    public init(session: String, acked: ChannelSeqs) {
        self.session = session
        self.acked = acked
    }

    enum CodingKeys: String, CodingKey, CaseIterable { case type, session, acked }

    public init(from decoder: Decoder) throws {
        try decoder.expectType(Self.typeTag, keys: CodingKeys.self)
        let c = try decoder.container(keyedBy: CodingKeys.self)
        session = try c.decode(String.self, forKey: .session)
        acked = try c.decode(ChannelSeqs.self, forKey: .acked)
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(Self.typeTag, forKey: .type)
        try c.encode(session, forKey: .session)
        try c.encode(acked, forKey: .acked)
    }
}

/// `ack` — cumulative: every frame `<= seq` on `channel` is durable on the server.
public struct Ack: Equatable, Sendable, Codable, TaggedMessage {
    public static let typeTag = "ack"

    public var channel: Channel
    public var seq: UInt32

    public init(channel: Channel, seq: UInt32) {
        self.channel = channel
        self.seq = seq
    }

    enum CodingKeys: String, CodingKey, CaseIterable { case type, channel, seq }

    public init(from decoder: Decoder) throws {
        try decoder.expectType(Self.typeTag, keys: CodingKeys.self)
        let c = try decoder.container(keyedBy: CodingKeys.self)
        channel = try c.decode(Channel.self, forKey: .channel)
        seq = try c.decode(UInt32.self, forKey: .seq)
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(Self.typeTag, forKey: .type)
        try c.encode(channel, forKey: .channel)
        try c.encode(seq, forKey: .seq)
    }
}

public enum Speaker: String, Codable, Sendable { case me, them }

/// `segment` — an authoritative transcript segment.
public struct Segment: Equatable, Sendable, Codable, TaggedMessage {
    public static let typeTag = "segment"

    public var id: String
    public var channel: Channel
    public var speaker: Speaker
    public var startMs: Int
    public var endMs: Int
    public var text: String
    public var sttTier: String

    public init(id: String, channel: Channel, speaker: Speaker, startMs: Int, endMs: Int, text: String, sttTier: String) {
        self.id = id
        self.channel = channel
        self.speaker = speaker
        self.startMs = startMs
        self.endMs = endMs
        self.text = text
        self.sttTier = sttTier
    }

    enum CodingKeys: String, CodingKey, CaseIterable {
        case type, id, channel, speaker, text
        case startMs = "start_ms"
        case endMs = "end_ms"
        case sttTier = "stt_tier"
    }

    public init(from decoder: Decoder) throws {
        try decoder.expectType(Self.typeTag, keys: CodingKeys.self)
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        channel = try c.decode(Channel.self, forKey: .channel)
        speaker = try c.decode(Speaker.self, forKey: .speaker)
        startMs = try c.decodeNonNegative(forKey: .startMs)
        endMs = try c.decodeNonNegative(forKey: .endMs)
        text = try c.decode(String.self, forKey: .text)
        sttTier = try c.decode(String.self, forKey: .sttTier)
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(Self.typeTag, forKey: .type)
        try c.encode(id, forKey: .id)
        try c.encode(channel, forKey: .channel)
        try c.encode(speaker, forKey: .speaker)
        try c.encode(startMs, forKey: .startMs)
        try c.encode(endMs, forKey: .endMs)
        try c.encode(text, forKey: .text)
        try c.encode(sttTier, forKey: .sttTier)
    }
}

// MARK: - Copilot

public struct CopilotNote: Equatable, Sendable, Codable, Identifiable {
    public var id: String
    public var text: String

    public init(id: String, text: String) {
        self.id = id
        self.text = text
    }

    enum CodingKeys: String, CodingKey, CaseIterable { case id, text }

    public init(from decoder: Decoder) throws {
        try decoder.rejectUnknownKeys(CodingKeys.self)
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        text = try c.decode(String.self, forKey: .text)
    }
}

public struct CopilotAction: Equatable, Sendable, Codable, Identifiable {
    public var id: String
    public var text: String
    public var owner: String?
    public var due: String?

    public init(id: String, text: String, owner: String? = nil, due: String? = nil) {
        self.id = id
        self.text = text
        self.owner = owner
        self.due = due
    }

    enum CodingKeys: String, CodingKey, CaseIterable { case id, text, owner, due }

    public init(from decoder: Decoder) throws {
        try decoder.rejectUnknownKeys(CodingKeys.self)
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        text = try c.decode(String.self, forKey: .text)
        owner = try c.decodeIfPresent(String.self, forKey: .owner)
        due = try c.decodeIfPresent(String.self, forKey: .due)
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(id, forKey: .id)
        try c.encode(text, forKey: .text)
        try c.encodeIfPresent(owner, forKey: .owner)
        try c.encodeIfPresent(due, forKey: .due)
    }
}

public enum SuggestionKind: String, Codable, Sendable {
    case question, gap, counterpoint
    case factCheck = "fact_check"
}

public struct CopilotSuggestion: Equatable, Sendable, Codable, Identifiable {
    public var id: String
    public var kind: SuggestionKind
    public var text: String
    public var expiresAtMs: Int

    public init(id: String, kind: SuggestionKind, text: String, expiresAtMs: Int) {
        self.id = id
        self.kind = kind
        self.text = text
        self.expiresAtMs = expiresAtMs
    }

    enum CodingKeys: String, CodingKey, CaseIterable {
        case id, kind, text
        case expiresAtMs = "expires_at_ms"
    }

    public init(from decoder: Decoder) throws {
        try decoder.rejectUnknownKeys(CodingKeys.self)
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        kind = try c.decode(SuggestionKind.self, forKey: .kind)
        text = try c.decode(String.self, forKey: .text)
        expiresAtMs = try c.decodeNonNegative(forKey: .expiresAtMs)
    }
}

public struct RelatedNote: Equatable, Sendable, Codable, Identifiable {
    public var path: String
    public var title: String
    public var snippet: String
    public var uri: String
    public var id: String { path }

    public init(path: String, title: String, snippet: String, uri: String) {
        self.path = path
        self.title = title
        self.snippet = snippet
        self.uri = uri
    }

    enum CodingKeys: String, CodingKey, CaseIterable { case path, title, snippet, uri }

    public init(from decoder: Decoder) throws {
        try decoder.rejectUnknownKeys(CodingKeys.self)
        let c = try decoder.container(keyedBy: CodingKeys.self)
        path = try c.decode(String.self, forKey: .path)
        title = try c.decode(String.self, forKey: .title)
        snippet = try c.decode(String.self, forKey: .snippet)
        uri = try c.decode(String.self, forKey: .uri)
    }
}

/// `copilot` — a full-state snapshot. The client never merges deltas; higher `version` wins.
public struct CopilotSnapshot: Equatable, Sendable, Codable, TaggedMessage {
    public static let typeTag = "copilot"

    public var version: Int
    public var notes: [CopilotNote]
    public var actions: [CopilotAction]
    public var decisions: [CopilotNote]
    public var suggestions: [CopilotSuggestion]
    public var related: [RelatedNote]

    public init(
        version: Int,
        notes: [CopilotNote] = [],
        actions: [CopilotAction] = [],
        decisions: [CopilotNote] = [],
        suggestions: [CopilotSuggestion] = [],
        related: [RelatedNote] = []
    ) {
        self.version = version
        self.notes = notes
        self.actions = actions
        self.decisions = decisions
        self.suggestions = suggestions
        self.related = related
    }

    enum CodingKeys: String, CodingKey, CaseIterable {
        case type, version, notes, actions, decisions, suggestions, related
    }

    public init(from decoder: Decoder) throws {
        try decoder.expectType(Self.typeTag, keys: CodingKeys.self)
        let c = try decoder.container(keyedBy: CodingKeys.self)
        version = try c.decodeNonNegative(forKey: .version)
        notes = try c.decode([CopilotNote].self, forKey: .notes)
        actions = try c.decode([CopilotAction].self, forKey: .actions)
        decisions = try c.decode([CopilotNote].self, forKey: .decisions)
        suggestions = try c.decode([CopilotSuggestion].self, forKey: .suggestions)
        related = try c.decode([RelatedNote].self, forKey: .related)
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(Self.typeTag, forKey: .type)
        try c.encode(version, forKey: .version)
        try c.encode(notes, forKey: .notes)
        try c.encode(actions, forKey: .actions)
        try c.encode(decisions, forKey: .decisions)
        try c.encode(suggestions, forKey: .suggestions)
        try c.encode(related, forKey: .related)
    }
}

/// `status` — pipeline health. `sttTier` is `nil` when no tier is active.
public struct Status: Equatable, Sendable, Codable, TaggedMessage {
    public static let typeTag = "status"

    public var sttTier: String?
    public var llmOk: Bool
    public var lagMs: Int

    public init(sttTier: String?, llmOk: Bool, lagMs: Int) {
        self.sttTier = sttTier
        self.llmOk = llmOk
        self.lagMs = lagMs
    }

    enum CodingKeys: String, CodingKey, CaseIterable {
        case type
        case sttTier = "stt_tier"
        case llmOk = "llm_ok"
        case lagMs = "lag_ms"
    }

    public init(from decoder: Decoder) throws {
        try decoder.expectType(Self.typeTag, keys: CodingKeys.self)
        let c = try decoder.container(keyedBy: CodingKeys.self)
        sttTier = try c.decodeRequiredNullable(String.self, forKey: .sttTier)
        llmOk = try c.decode(Bool.self, forKey: .llmOk)
        lagMs = try c.decodeNonNegative(forKey: .lagMs)
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(Self.typeTag, forKey: .type)
        try c.encode(sttTier, forKey: .sttTier)
        try c.encode(llmOk, forKey: .llmOk)
        try c.encode(lagMs, forKey: .lagMs)
    }
}

/// `final_note` — the note the server wrote when the session ended.
public struct FinalNote: Equatable, Sendable, Codable, TaggedMessage {
    public static let typeTag = "final_note"

    public var path: String
    public var title: String

    public init(path: String, title: String) {
        self.path = path
        self.title = title
    }

    enum CodingKeys: String, CodingKey, CaseIterable { case type, path, title }

    public init(from decoder: Decoder) throws {
        try decoder.expectType(Self.typeTag, keys: CodingKeys.self)
        let c = try decoder.container(keyedBy: CodingKeys.self)
        path = try c.decode(String.self, forKey: .path)
        title = try c.decode(String.self, forKey: .title)
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(Self.typeTag, forKey: .type)
        try c.encode(path, forKey: .path)
        try c.encode(title, forKey: .title)
    }
}

/// `error` — a server-reported error.
public struct ServerError: Equatable, Sendable, Codable, TaggedMessage, Error {
    public static let typeTag = "error"

    public var code: String
    public var message: String

    public init(code: String, message: String) {
        self.code = code
        self.message = message
    }

    enum CodingKeys: String, CodingKey, CaseIterable { case type, code, message }

    public init(from decoder: Decoder) throws {
        try decoder.expectType(Self.typeTag, keys: CodingKeys.self)
        let c = try decoder.container(keyedBy: CodingKeys.self)
        code = try c.decode(String.self, forKey: .code)
        message = try c.decode(String.self, forKey: .message)
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(Self.typeTag, forKey: .type)
        try c.encode(code, forKey: .code)
        try c.encode(message, forKey: .message)
    }
}

/// Any JSON message the server sends over the WebSocket, discriminated by `type`.
public enum ServerEvent: Equatable, Sendable, Codable {
    case helloAck(HelloAck)
    case ack(Ack)
    case segment(Segment)
    case copilot(CopilotSnapshot)
    case status(Status)
    case finalNote(FinalNote)
    case error(ServerError)

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: AnyCodingKey.self)
        let tag = try c.decode(String.self, forKey: AnyCodingKey(stringValue: "type")!)
        switch tag {
        case HelloAck.typeTag: self = .helloAck(try HelloAck(from: decoder))
        case Ack.typeTag: self = .ack(try Ack(from: decoder))
        case Segment.typeTag: self = .segment(try Segment(from: decoder))
        case CopilotSnapshot.typeTag: self = .copilot(try CopilotSnapshot(from: decoder))
        case Status.typeTag: self = .status(try Status(from: decoder))
        case FinalNote.typeTag: self = .finalNote(try FinalNote(from: decoder))
        case ServerError.typeTag: self = .error(try ServerError(from: decoder))
        default:
            throw DecodingError.dataCorrupted(.init(
                codingPath: decoder.codingPath, debugDescription: "Unknown server event type \"\(tag)\""
            ))
        }
    }

    public func encode(to encoder: Encoder) throws {
        switch self {
        case .helloAck(let m): try m.encode(to: encoder)
        case .ack(let m): try m.encode(to: encoder)
        case .segment(let m): try m.encode(to: encoder)
        case .copilot(let m): try m.encode(to: encoder)
        case .status(let m): try m.encode(to: encoder)
        case .finalNote(let m): try m.encode(to: encoder)
        case .error(let m): try m.encode(to: encoder)
        }
    }
}
