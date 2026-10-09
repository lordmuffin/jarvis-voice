import Foundation

public enum SessionMode: String, Codable, Sendable { case meeting, solo }

/// `POST /v1/sessions` body.
public struct SessionCreateRequest: Equatable, Sendable, Codable {
    public var title: String?
    public var mode: SessionMode
    /// Either `[.mic]` or `[.mic, .system]`.
    public var channels: [Channel]

    public init(title: String? = nil, mode: SessionMode, channels: [Channel]) {
        self.title = title
        self.mode = mode
        self.channels = channels
    }

    enum CodingKeys: String, CodingKey, CaseIterable { case title, mode, channels }

    public init(from decoder: Decoder) throws {
        try decoder.rejectUnknownKeys(CodingKeys.self)
        let c = try decoder.container(keyedBy: CodingKeys.self)
        title = try c.decodeIfPresent(String.self, forKey: .title)
        mode = try c.decode(SessionMode.self, forKey: .mode)
        channels = try c.decode([Channel].self, forKey: .channels)
        guard channels == [.mic] || channels == [.mic, .system] else {
            throw DecodingError.dataCorruptedError(
                forKey: .channels, in: c, debugDescription: "channels must be [mic] or [mic, system]"
            )
        }
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encodeIfPresent(title, forKey: .title)
        try c.encode(mode, forKey: .mode)
        try c.encode(channels, forKey: .channels)
    }
}

/// `POST /v1/sessions` response.
public struct SessionCreateResponse: Equatable, Sendable, Codable {
    /// The session UUID as the server spelled it (kept verbatim so re-encoding is lossless).
    public var id: String

    public init(id: String) { self.id = id }

    enum CodingKeys: String, CodingKey, CaseIterable { case id }

    public init(from decoder: Decoder) throws {
        try decoder.rejectUnknownKeys(CodingKeys.self)
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        guard UUID(uuidString: id) != nil else {
            throw DecodingError.dataCorruptedError(
                forKey: .id, in: c, debugDescription: "id is not a UUID"
            )
        }
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(id, forKey: .id)
    }
}

public enum TicketRole: String, Codable, Sendable { case producer, viewer }

/// `POST /v1/sessions/{id}/ticket` body.
public struct TicketRequest: Equatable, Sendable, Codable {
    public var role: TicketRole

    public init(role: TicketRole) { self.role = role }

    enum CodingKeys: String, CodingKey, CaseIterable { case role }

    public init(from decoder: Decoder) throws {
        try decoder.rejectUnknownKeys(CodingKeys.self)
        let c = try decoder.container(keyedBy: CodingKeys.self)
        role = try c.decode(TicketRole.self, forKey: .role)
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(role, forKey: .role)
    }
}

/// `POST /v1/sessions/{id}/ticket` response.
public struct TicketResponse: Equatable, Sendable, Codable {
    public static let expiresIn = 60

    public var ticket: String

    public init(ticket: String) { self.ticket = ticket }

    enum CodingKeys: String, CodingKey, CaseIterable {
        case ticket
        case expiresIn = "expires_in"
    }

    public init(from decoder: Decoder) throws {
        try decoder.rejectUnknownKeys(CodingKeys.self)
        let c = try decoder.container(keyedBy: CodingKeys.self)
        ticket = try c.decode(String.self, forKey: .ticket)
        guard !ticket.isEmpty else {
            throw DecodingError.dataCorruptedError(
                forKey: .ticket, in: c, debugDescription: "ticket is empty"
            )
        }
        let expires = try c.decode(Int.self, forKey: .expiresIn)
        guard expires == Self.expiresIn else {
            throw DecodingError.dataCorruptedError(
                forKey: .expiresIn, in: c, debugDescription: "expires_in must be \(Self.expiresIn)"
            )
        }
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(ticket, forKey: .ticket)
        try c.encode(Self.expiresIn, forKey: .expiresIn)
    }
}
