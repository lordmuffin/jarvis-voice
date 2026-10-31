import Foundation
import Testing
@testable import JarvisLiveKit

/// Counts the requests that reach the network layer and answers each with an empty 200.
private final class CountingURLProtocol: URLProtocol, @unchecked Sendable {
    nonisolated(unsafe) static var requests = 0

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        Self.requests += 1
        let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: "HTTP/1.1", headerFields: nil)!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        // The strict response types reject unknown keys, so each path gets exactly its own body.
        let body = request.url?.path == "/healthz"
            ? #"{"status":"ok"}"#
            : #"{"id":"3f2b8c1e-7a4d-4e6b-9c0a-1d2e3f4a5b6c"}"#
        client?.urlProtocol(self, didLoad: Data(body.utf8))
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}
}

private func countingSession() -> URLSession {
    let configuration = URLSessionConfiguration.ephemeral
    configuration.protocolClasses = [CountingURLProtocol.self]
    return URLSession(configuration: configuration)
}

@Suite("EgressGate", .serialized)
struct EgressGateTests {
    private let base = URL(string: "https://live.example.test")!

    @Test func blocksWhileALocalSessionRecordsAndNotAfter() {
        let gate = EgressGate()
        #expect(!gate.isBlocking)
        gate.beginLocalSession()
        #expect(gate.isBlocking)
        #expect(throws: EgressBlocked.self) { try gate.check() }
        gate.endLocalSession()
        #expect(!gate.isBlocking)
        #expect(throws: Never.self) { try gate.check() }
    }

    @Test func overlappingSessionsKeepItShutUntilTheLastEnds() {
        let gate = EgressGate()
        gate.beginLocalSession()
        gate.beginLocalSession()
        gate.endLocalSession()
        #expect(gate.isBlocking)
        gate.endLocalSession()
        gate.endLocalSession()  // an extra end never goes below zero
        #expect(!gate.isBlocking)
    }

    @Test func theAPIClientSendsNothingWhileBlocked() async throws {
        CountingURLProtocol.requests = 0
        let gate = EgressGate()
        let client = LiveAPIClient(baseURL: base, session: countingSession(), gate: gate, bearerToken: { "t" })
        gate.beginLocalSession()
        await #expect(throws: EgressBlocked.self) {
            _ = try await client.createSession(SessionCreateRequest(mode: .meeting, channels: [.mic]))
        }
        await #expect(throws: EgressBlocked.self) { _ = try await client.ticket(sessionId: "x") }
        #expect(CountingURLProtocol.requests == 0)

        gate.endLocalSession()
        _ = try await client.createSession(SessionCreateRequest(mode: .meeting, channels: [.mic]))
        #expect(CountingURLProtocol.requests == 1)
    }

    @Test func healthChecksAreSkippedWhileBlocked() async {
        CountingURLProtocol.requests = 0
        let gate = EgressGate()
        gate.beginLocalSession()
        let session = countingSession()
        let info = await ServerInfo.fetch(baseURL: base, session: session, gate: gate)
        let reachable = await NetworkPlaceDetector.isReachable(baseURL: base, session: session, gate: gate)
        #expect(info == nil && !reachable)
        #expect(CountingURLProtocol.requests == 0)

        gate.endLocalSession()
        #expect(await NetworkPlaceDetector.isReachable(baseURL: base, session: session, gate: gate))
        #expect(CountingURLProtocol.requests == 1)
    }

    @Test func aRestrictedCreateRequestEncodesTheFlagOnlyWhenSet() throws {
        let plain = try JSONEncoder().encode(SessionCreateRequest(mode: .solo, channels: [.mic]))
        #expect(!String(decoding: plain, as: UTF8.self).contains("restricted"))
        let marked = try JSONEncoder().encode(SessionCreateRequest(mode: .solo, channels: [.mic], restricted: true))
        #expect(String(decoding: marked, as: UTF8.self).contains(#""restricted":true"#))
    }
}
