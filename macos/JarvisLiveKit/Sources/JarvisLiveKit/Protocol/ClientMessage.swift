import Foundation

/// `hello` — first message on a connection. `resume` is the last seq the client believes was acked.
public struct Hello: Equatable, Sendable, Codable, TaggedMessage {
    public static let typeTag = "hello"
    public static let protocolVersion = 1
    public static let codec = "pcm16le_16k"

    public var device: String
    public var resume: ChannelSeqs

    public init(device: String, resume: ChannelSeqs) {
        self.device = device
        self.resume = resume
    }

    enum CodingKeys: String, CodingKey, CaseIterable { case type, `protocol`, device, codec, resume }

    public init(from decoder: Decoder) throws {
        try decoder.expectType(Self.typeTag, keys: CodingKeys.self)
        let c = try decoder.container(keyedBy: CodingKeys.self)
        let version = try c.decode(Int.self, forKey: .protocol)
        guard version == Self.protocolVersion else {
            throw DecodingError.dataCorruptedError(
                forKey: .protocol, in: c, debugDescription: "Unsupported protocol \(version)"
            )
        }
        let codec = try c.decode(String.self, forKey: .codec)
        guard codec == Self.codec else {
            throw DecodingError.dataCorruptedError(
                forKey: .codec, in: c, debugDescription: "Unsupported codec \(codec)"
            )
        }
        device = try c.decode(String.self, forKey: .device)
        resume = try c.decode(ChannelSeqs.self, forKey: .resume)
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(Self.typeTag, forKey: .type)
        try c.encode(Self.protocolVersion, forKey: .protocol)
        try c.encode(device, forKey: .device)
        try c.encode(Self.codec, forKey: .codec)
        try c.encode(resume, forKey: .resume)
    }
}

/// `draft_segment` — client-side draft transcript.
public struct DraftSegment: Equatable, Sendable, Codable, TaggedMessage {
    public static let typeTag = "draft_segment"

    public var channel: Channel
    public var startMs: Int
    public var endMs: Int
    public var text: String
    public var final: Bool

    public init(channel: Channel, startMs: Int, endMs: Int, text: String, final: Bool) {
        self.channel = channel
        self.startMs = startMs
        self.endMs = endMs
        self.text = text
        self.final = final
    }

    enum CodingKeys: String, CodingKey, CaseIterable {
        case type, channel, text, final
        case startMs = "start_ms"
        case endMs = "end_ms"
    }

    public init(from decoder: Decoder) throws {
        try decoder.expectType(Self.typeTag, keys: CodingKeys.self)
        let c = try decoder.container(keyedBy: CodingKeys.self)
        channel = try c.decode(Channel.self, forKey: .channel)
        startMs = try c.decodeNonNegative(forKey: .startMs)
        endMs = try c.decodeNonNegative(forKey: .endMs)
        text = try c.decode(String.self, forKey: .text)
        final = try c.decode(Bool.self, forKey: .final)
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(Self.typeTag, forKey: .type)
        try c.encode(channel, forKey: .channel)
        try c.encode(startMs, forKey: .startMs)
        try c.encode(endMs, forKey: .endMs)
        try c.encode(text, forKey: .text)
        try c.encode(final, forKey: .final)
    }
}

/// `marker` — a labelled point in session time.
public struct Marker: Equatable, Sendable, Codable, TaggedMessage {
    public static let typeTag = "marker"

    public var tMs: Int
    public var label: String

    public init(tMs: Int, label: String) {
        self.tMs = tMs
        self.label = label
    }

    enum CodingKeys: String, CodingKey, CaseIterable {
        case type, label
        case tMs = "t_ms"
    }

    public init(from decoder: Decoder) throws {
        try decoder.expectType(Self.typeTag, keys: CodingKeys.self)
        let c = try decoder.container(keyedBy: CodingKeys.self)
        tMs = try c.decodeNonNegative(forKey: .tMs)
        label = try c.decode(String.self, forKey: .label)
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(Self.typeTag, forKey: .type)
        try c.encode(tMs, forKey: .tMs)
        try c.encode(label, forKey: .label)
    }
}

/// `end` — no more audio will follow.
public struct End: Equatable, Sendable, Codable, TaggedMessage {
    public static let typeTag = "end"

    public init() {}

    enum CodingKeys: String, CodingKey, CaseIterable { case type }

    public init(from decoder: Decoder) throws {
        try decoder.expectType(Self.typeTag, keys: CodingKeys.self)
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(Self.typeTag, forKey: .type)
    }
}

/// Any JSON message a client sends over the WebSocket, discriminated by `type`.
public enum ClientMessage: Equatable, Sendable, Codable {
    case hello(Hello)
    case draftSegment(DraftSegment)
    case marker(Marker)
    case end

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: AnyCodingKey.self)
        let tag = try c.decode(String.self, forKey: AnyCodingKey(stringValue: "type")!)
        switch tag {
        case Hello.typeTag: self = .hello(try Hello(from: decoder))
        case DraftSegment.typeTag: self = .draftSegment(try DraftSegment(from: decoder))
        case Marker.typeTag: self = .marker(try Marker(from: decoder))
        case End.typeTag:
            _ = try End(from: decoder)
            self = .end
        default:
            throw DecodingError.dataCorrupted(.init(
                codingPath: decoder.codingPath, debugDescription: "Unknown client message type \"\(tag)\""
            ))
        }
    }

    public func encode(to encoder: Encoder) throws {
        switch self {
        case .hello(let m): try m.encode(to: encoder)
        case .draftSegment(let m): try m.encode(to: encoder)
        case .marker(let m): try m.encode(to: encoder)
        case .end: try End().encode(to: encoder)
        }
    }
}
