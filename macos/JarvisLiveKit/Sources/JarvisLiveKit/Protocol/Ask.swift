import Foundation

// Hold to ask (docs/plans/voice-push-to-talk.md). The client marks the ask on its audio frames'
// own clock; the reply is text, spoken on this Mac. The server sends `voice_state` and
// `ask_reply` only to a client that put `voice_ask` in the stream URL's `features`.

extension KeyedDecodingContainer {
    /// Decodes an ask id: 1 to 64 characters.
    func decodeAskId(forKey key: Key) throws -> String {
        let value = try decode(String.self, forKey: key)
        guard (1...64).contains(value.count) else {
            throw DecodingError.dataCorruptedError(
                forKey: key, in: self, debugDescription: "An ask id is 1 to 64 characters"
            )
        }
        return value
    }
}

/// `ask_begin`: the ask key went down.
public struct AskBegin: Equatable, Sendable, Codable, TaggedMessage {
    public static let typeTag = "ask_begin"

    public var id: String
    public var tMs: Int

    public init(id: String, tMs: Int) {
        self.id = id
        self.tMs = tMs
    }

    enum CodingKeys: String, CodingKey, CaseIterable {
        case type, id
        case tMs = "t_ms"
    }

    public init(from decoder: Decoder) throws {
        try decoder.expectType(Self.typeTag, keys: CodingKeys.self)
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decodeAskId(forKey: .id)
        tMs = try c.decodeNonNegative(forKey: .tMs)
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(Self.typeTag, forKey: .type)
        try c.encode(id, forKey: .id)
        try c.encode(tMs, forKey: .tMs)
    }
}

/// `ask_end`: the ask key came up.
public struct AskEnd: Equatable, Sendable, Codable, TaggedMessage {
    public static let typeTag = "ask_end"

    public var id: String
    public var tMs: Int

    public init(id: String, tMs: Int) {
        self.id = id
        self.tMs = tMs
    }

    enum CodingKeys: String, CodingKey, CaseIterable {
        case type, id
        case tMs = "t_ms"
    }

    public init(from decoder: Decoder) throws {
        try decoder.expectType(Self.typeTag, keys: CodingKeys.self)
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decodeAskId(forKey: .id)
        tMs = try c.decodeNonNegative(forKey: .tMs)
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(Self.typeTag, forKey: .type)
        try c.encode(id, forKey: .id)
        try c.encode(tMs, forKey: .tMs)
    }
}

public enum AskPlayedState: String, Codable, Sendable { case started, done, skipped }

public enum AskSkipReason: String, Codable, Sendable {
    case route, muted, stale, interrupted, superseded
    case ttsFailed = "tts_failed"
}

/// `ask_played`: what this client did with a reply. `latencyMs` is key up to first audio.
public struct AskPlayed: Equatable, Sendable, Codable, TaggedMessage {
    public static let typeTag = "ask_played"

    public var id: String
    public var state: AskPlayedState
    public var tMs: Int
    public var latencyMs: Int?
    public var reason: AskSkipReason?

    public init(id: String, state: AskPlayedState, tMs: Int, latencyMs: Int? = nil, reason: AskSkipReason? = nil) {
        self.id = id
        self.state = state
        self.tMs = tMs
        self.latencyMs = latencyMs
        self.reason = reason
    }

    enum CodingKeys: String, CodingKey, CaseIterable {
        case type, id, state, reason
        case tMs = "t_ms"
        case latencyMs = "latency_ms"
    }

    public init(from decoder: Decoder) throws {
        try decoder.expectType(Self.typeTag, keys: CodingKeys.self)
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decodeAskId(forKey: .id)
        state = try c.decode(AskPlayedState.self, forKey: .state)
        tMs = try c.decodeNonNegative(forKey: .tMs)
        latencyMs = c.contains(.latencyMs) ? try c.decodeNonNegative(forKey: .latencyMs) : nil
        reason = try c.decodeIfPresent(AskSkipReason.self, forKey: .reason)
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(Self.typeTag, forKey: .type)
        try c.encode(id, forKey: .id)
        try c.encode(state, forKey: .state)
        try c.encode(tMs, forKey: .tMs)
        try c.encodeIfPresent(latencyMs, forKey: .latencyMs)
        try c.encodeIfPresent(reason, forKey: .reason)
    }
}

/// `voice_state`: whether this session takes asks. The ask control shows only after `true`.
public struct VoiceState: Equatable, Sendable, Codable, TaggedMessage {
    public static let typeTag = "voice_state"
    /// Value of the stream URL's `features` parameter that asks for this and `ask_reply`.
    public static let feature = "voice_ask"

    public var available: Bool

    public init(available: Bool) { self.available = available }

    enum CodingKeys: String, CodingKey, CaseIterable { case type, available }

    public init(from decoder: Decoder) throws {
        try decoder.expectType(Self.typeTag, keys: CodingKeys.self)
        let c = try decoder.container(keyedBy: CodingKeys.self)
        available = try c.decode(Bool.self, forKey: .available)
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(Self.typeTag, forKey: .type)
        try c.encode(available, forKey: .available)
    }
}

public enum AskOutcome: String, Codable, Sendable {
    case answer, filed, empty, timeout, failed
    case noStt = "no_stt"
}

/// `ask_reply`: the answer to one ask, to be spoken here. Never part of the transcript.
public struct AskReply: Equatable, Sendable, Codable, TaggedMessage {
    public static let typeTag = "ask_reply"

    public var id: String
    public var outcome: AskOutcome
    public var text: String

    public init(id: String, outcome: AskOutcome, text: String) {
        self.id = id
        self.outcome = outcome
        self.text = text
    }

    enum CodingKeys: String, CodingKey, CaseIterable { case type, id, outcome, text }

    public init(from decoder: Decoder) throws {
        try decoder.expectType(Self.typeTag, keys: CodingKeys.self)
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decodeAskId(forKey: .id)
        outcome = try c.decode(AskOutcome.self, forKey: .outcome)
        text = try c.decode(String.self, forKey: .text)
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(Self.typeTag, forKey: .type)
        try c.encode(id, forKey: .id)
        try c.encode(outcome, forKey: .outcome)
        try c.encode(text, forKey: .text)
    }
}
