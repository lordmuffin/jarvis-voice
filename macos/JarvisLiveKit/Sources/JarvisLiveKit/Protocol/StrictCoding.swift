import Foundation

/// A coding key that accepts any string, used to enumerate the keys present in a JSON object.
struct AnyCodingKey: CodingKey {
    let stringValue: String
    var intValue: Int? { nil }
    init?(stringValue: String) { self.stringValue = stringValue }
    init?(intValue: Int) { nil }
}

/// Implemented by every message that carries a `type` discriminator on the wire.
public protocol TaggedMessage {
    static var typeTag: String { get }
}

extension Decoder {
    /// Throws if the object contains a key outside `Keys` (the protocol sets
    /// `additionalProperties: false` on every object).
    func rejectUnknownKeys<Keys: CodingKey & CaseIterable>(_: Keys.Type) throws {
        let allowed = Set(Keys.allCases.map(\.stringValue))
        let container = try container(keyedBy: AnyCodingKey.self)
        for key in container.allKeys where !allowed.contains(key.stringValue) {
            throw DecodingError.dataCorruptedError(
                forKey: key, in: container,
                debugDescription: "Unknown field \"\(key.stringValue)\""
            )
        }
    }

    /// Rejects unknown keys and verifies the `type` discriminator equals `tag`.
    func expectType<Keys: CodingKey & CaseIterable>(_ tag: String, keys: Keys.Type) throws {
        try rejectUnknownKeys(keys)
        let container = try container(keyedBy: AnyCodingKey.self)
        let key = AnyCodingKey(stringValue: "type")!
        let actual = try container.decode(String.self, forKey: key)
        guard actual == tag else {
            throw DecodingError.dataCorruptedError(
                forKey: key, in: container,
                debugDescription: "Expected type \"\(tag)\", got \"\(actual)\""
            )
        }
    }
}

extension KeyedDecodingContainer {
    /// Decodes an integer that the schema constrains to `minimum: 0`.
    func decodeNonNegative(forKey key: Key) throws -> Int {
        let value = try decode(Int.self, forKey: key)
        guard value >= 0 else {
            throw DecodingError.dataCorruptedError(
                forKey: key, in: self, debugDescription: "Expected a non-negative integer"
            )
        }
        return value
    }

    /// Decodes a key that must be present but may be `null` (schema: `type: [integer, null]`).
    /// Unlike `decodeIfPresent`, a missing key throws.
    func decodeRequiredNullable<T: Decodable>(_: T.Type, forKey key: Key) throws -> T? {
        try decode(T?.self, forKey: key)
    }
}
