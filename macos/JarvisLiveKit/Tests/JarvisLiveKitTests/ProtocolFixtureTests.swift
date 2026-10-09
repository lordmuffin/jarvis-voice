import Foundation
import Testing
@testable import JarvisLiveKit

private let clientTypes: Set<String> = ["hello", "draft_segment", "marker", "end"]
private let serverTypes: Set<String> = [
    "hello_ack", "ack", "segment", "copilot", "status", "final_note", "error",
]

private struct UnknownSchema: Error { let name: String }

/// Decodes `data` as the type named by the fixture's schema and re-encodes it.
private func roundTrip(schema: String, data: Data) throws -> Data {
    let decoder = JSONDecoder()
    let encoder = JSONEncoder()
    switch schema {
    case "session_create_request":
        return try encoder.encode(decoder.decode(SessionCreateRequest.self, from: data))
    case "session_create_response":
        return try encoder.encode(decoder.decode(SessionCreateResponse.self, from: data))
    case "ticket_request":
        return try encoder.encode(decoder.decode(TicketRequest.self, from: data))
    case "ticket_response":
        return try encoder.encode(decoder.decode(TicketResponse.self, from: data))
    case _ where clientTypes.contains(schema):
        return try encoder.encode(decoder.decode(ClientMessage.self, from: data))
    case _ where serverTypes.contains(schema):
        return try encoder.encode(decoder.decode(ServerEvent.self, from: data))
    default:
        throw UnknownSchema(name: schema)
    }
}

@Suite("Protocol fixtures")
struct ProtocolFixtureTests {
    @Test func fixtureDirectoriesAreFound() {
        #expect(Fixtures.valid.count >= 19, "valid fixtures missing — is #filePath-relative lookup broken?")
        #expect(Fixtures.invalid.count >= 17)
    }

    @Test("valid fixture decodes and re-encodes to equivalent JSON", arguments: Fixtures.valid)
    func validRoundTrips(url: URL) throws {
        let original = try Data(contentsOf: url)
        let reencoded = try roundTrip(schema: Fixtures.schemaName(of: url), data: original)
        let a = try JSONSerialization.jsonObject(with: original) as? NSDictionary
        let b = try JSONSerialization.jsonObject(with: reencoded) as? NSDictionary
        #expect(a != nil && a == b, "\(url.lastPathComponent): \(String(decoding: reencoded, as: UTF8.self))")
    }

    @Test("invalid fixture fails to decode", arguments: Fixtures.invalid)
    func invalidIsRejected(url: URL) throws {
        let data = try Data(contentsOf: url)
        let schema = Fixtures.schemaName(of: url)
        #expect(throws: DecodingError.self, "\(url.lastPathComponent) should be rejected") {
            try roundTrip(schema: schema, data: data)
        }
    }

    @Test func everyServerAndClientTypeHasAValidFixture() {
        let covered = Set(Fixtures.valid.map(Fixtures.schemaName(of:)))
        #expect(clientTypes.union(serverTypes).isSubset(of: covered))
    }

    @Test func typedAccessMatchesFixtureValues() throws {
        let data = try Data(contentsOf: Fixtures.root.appending(path: "valid/hello.json"))
        guard case .hello(let hello) = try JSONDecoder().decode(ClientMessage.self, from: data) else {
            Issue.record("not a hello")
            return
        }
        #expect(hello.device == "macbook-pro")
        #expect(hello.resume == ChannelSeqs(mic: 41, system: nil))
    }

    @Test func helloEncodesNullResumeExplicitly() throws {
        let data = try JSONEncoder().encode(ClientMessage.hello(Hello(device: "d", resume: ChannelSeqs())))
        let object = try #require(try JSONSerialization.jsonObject(with: data) as? [String: Any])
        let resume = try #require(object["resume"] as? [String: Any])
        #expect(resume.keys.sorted() == ["mic", "system"])
        #expect(resume["mic"] is NSNull)
    }
}
