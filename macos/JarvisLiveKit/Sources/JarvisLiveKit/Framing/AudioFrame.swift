import Foundation

public enum FrameError: Error, Equatable, Sendable {
    case tooShort(Int)
    case unsupportedVersion(UInt8)
    case unknownChannel(UInt8)
    case payloadSize(Int)
}

/// A binary audio frame: 12-byte little-endian header followed by PCM16LE/16 kHz/mono payload.
///
///     offset 0  u8   version (1)
///     offset 1  u8   channel (0 mic, 1 system)
///     offset 2  u16  flags (0)
///     offset 4  u32  seq (per channel, +1 per frame)
///     offset 8  u32  t_ms (capture time since session start)
public struct AudioFrame: Equatable, Sendable {
    public static let version: UInt8 = 1
    public static let headerSize = 12
    public static let bytesPerMs = 32
    public static let minPayloadBytes = 20 * bytesPerMs   // 640
    public static let maxPayloadBytes = 200 * bytesPerMs  // 6400

    public var channel: Channel
    public var seq: UInt32
    public var tMs: UInt32
    public var payload: Data

    public init(channel: Channel, seq: UInt32, tMs: UInt32, payload: Data) {
        self.channel = channel
        self.seq = seq
        self.tMs = tMs
        self.payload = payload
    }

    /// Audio duration of the payload in milliseconds.
    public var durationMs: Int { payload.count / Self.bytesPerMs }

    public static func isValidPayloadSize(_ count: Int) -> Bool {
        (minPayloadBytes...maxPayloadBytes).contains(count) && count.isMultiple(of: 2)
    }

    /// Serialises header + payload. Throws if the payload is outside the 20–200 ms range.
    public func encoded() throws -> Data {
        guard Self.isValidPayloadSize(payload.count) else { throw FrameError.payloadSize(payload.count) }
        var data = Data(capacity: Self.headerSize + payload.count)
        data.append(Self.version)
        data.append(channel.wireIndex)
        data.appendLE(UInt16(0))
        data.appendLE(seq)
        data.appendLE(tMs)
        data.append(payload)
        return data
    }

    /// Parses a frame, validating version, channel and payload size.
    public init(decoding data: Data) throws {
        guard data.count >= Self.headerSize else { throw FrameError.tooShort(data.count) }
        let bytes = [UInt8](data.prefix(Self.headerSize))
        guard bytes[0] == Self.version else { throw FrameError.unsupportedVersion(bytes[0]) }
        guard let channel = Channel(wireIndex: bytes[1]) else { throw FrameError.unknownChannel(bytes[1]) }
        // bytes[2..<4] are flags; reserved, ignored on receive.
        let payload = data.dropFirst(Self.headerSize)
        guard Self.isValidPayloadSize(payload.count) else { throw FrameError.payloadSize(payload.count) }
        self.channel = channel
        self.seq = UInt32(littleEndianBytes: bytes[4..<8])
        self.tMs = UInt32(littleEndianBytes: bytes[8..<12])
        self.payload = Data(payload)
    }
}

extension Data {
    fileprivate mutating func appendLE<T: FixedWidthInteger>(_ value: T) {
        var le = value.littleEndian
        Swift.withUnsafeBytes(of: &le) { append(contentsOf: $0) }
    }
}

extension UInt32 {
    fileprivate init(littleEndianBytes b: ArraySlice<UInt8>) {
        let i = b.startIndex
        self = UInt32(b[i]) | UInt32(b[i + 1]) << 8 | UInt32(b[i + 2]) << 16 | UInt32(b[i + 3]) << 24
    }
}
