import Foundation

/// An audio channel. The wire index (binary header byte 1) is `0` mic, `1` system.
public enum Channel: String, Codable, Sendable, CaseIterable, Hashable {
    case mic
    case system

    public var wireIndex: UInt8 {
        switch self {
        case .mic: 0
        case .system: 1
        }
    }

    public init?(wireIndex: UInt8) {
        switch wireIndex {
        case 0: self = .mic
        case 1: self = .system
        default: return nil
        }
    }
}

/// Last seq per channel; `nil` means "none". Both keys are required on the wire.
public struct ChannelSeqs: Equatable, Sendable, Codable {
    public var mic: UInt32?
    public var system: UInt32?

    public init(mic: UInt32? = nil, system: UInt32? = nil) {
        self.mic = mic
        self.system = system
    }

    public subscript(channel: Channel) -> UInt32? {
        get { channel == .mic ? mic : system }
        set { if channel == .mic { mic = newValue } else { system = newValue } }
    }

    enum CodingKeys: String, CodingKey, CaseIterable { case mic, system }

    public init(from decoder: Decoder) throws {
        try decoder.rejectUnknownKeys(CodingKeys.self)
        let c = try decoder.container(keyedBy: CodingKeys.self)
        mic = try c.decodeRequiredNullable(UInt32.self, forKey: .mic)
        system = try c.decodeRequiredNullable(UInt32.self, forKey: .system)
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        // `encode` (not `encodeIfPresent`) so nil is written as an explicit null.
        try c.encode(mic, forKey: .mic)
        try c.encode(system, forKey: .system)
    }
}
