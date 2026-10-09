import Foundation
import Testing
@testable import JarvisLiveKit

/// Intercepts requests on sessions configured with it and replies from `handler`.
private final class StubURLProtocol: URLProtocol, @unchecked Sendable {
    struct Captured: Sendable {
        var method: String?
        var path: String
        var authorization: String?
        var contentType: String?
        var body: Data
    }

    nonisolated(unsafe) static var handler: (@Sendable (Captured) -> (status: Int, body: Data))?

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        var body = request.httpBody ?? Data()
        if let stream = request.httpBodyStream {
            stream.open()
            var buffer = [UInt8](repeating: 0, count: 4096)
            while stream.hasBytesAvailable {
                let n = stream.read(&buffer, maxLength: buffer.count)
                if n <= 0 { break }
                body.append(buffer, count: n)
            }
            stream.close()
        }
        let captured = Captured(
            method: request.httpMethod,
            path: request.url?.path ?? "",
            authorization: request.value(forHTTPHeaderField: "Authorization"),
            contentType: request.value(forHTTPHeaderField: "Content-Type"),
            body: body
        )
        let reply = Self.handler?(captured) ?? (status: 500, body: Data())
        let response = HTTPURLResponse(
            url: request.url!, statusCode: reply.status, httpVersion: "HTTP/1.1", headerFields: nil
        )!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: reply.body)
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}
}

@Suite("LiveAPIClient", .serialized)
struct LiveAPIClientTests {
    private func makeClient() -> LiveAPIClient {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [StubURLProtocol.self]
        return LiveAPIClient(
            baseURL: URL(string: "https://live.example.test")!,
            session: URLSession(configuration: configuration),
            bearerToken: { "secret-token" }
        )
    }

    @Test func ticketRequestUsesBearerTokenAndDecodesResponse() async throws {
        let captured = Mutex<StubURLProtocol.Captured?>(nil)
        StubURLProtocol.handler = { request in
            captured.withLock { $0 = request }
            return (200, Data(#"{"ticket":"tkt_abc","expires_in":60}"#.utf8))
        }
        let client = makeClient()
        let response = try await client.ticket(sessionId: "3f2b8c1e-5d4a-4b7e-9a10-2c6d8e9f0a1b")

        #expect(response.ticket == "tkt_abc")
        let request = try #require(captured.withLock { $0 })
        #expect(request.method == "POST")
        #expect(request.path == "/v1/sessions/3f2b8c1e-5d4a-4b7e-9a10-2c6d8e9f0a1b/ticket")
        #expect(request.authorization == "Bearer secret-token")
        #expect(request.contentType == "application/json")
        let body = try JSONDecoder().decode(TicketRequest.self, from: request.body)
        #expect(body.role == .producer)
    }

    @Test func createSessionPostsRequestAndDecodesId() async throws {
        let captured = Mutex<StubURLProtocol.Captured?>(nil)
        StubURLProtocol.handler = { request in
            captured.withLock { $0 = request }
            return (201, Data(#"{"id":"3f2b8c1e-5d4a-4b7e-9a10-2c6d8e9f0a1b"}"#.utf8))
        }
        let response = try await makeClient().createSession(
            SessionCreateRequest(title: "Weekly sync", mode: .meeting, channels: [.mic, .system])
        )
        #expect(response.id == "3f2b8c1e-5d4a-4b7e-9a10-2c6d8e9f0a1b")
        let request = try #require(captured.withLock { $0 })
        #expect(request.path == "/v1/sessions")
        let body = try JSONDecoder().decode(SessionCreateRequest.self, from: request.body)
        #expect(body.channels == [.mic, .system])
        #expect(body.title == "Weekly sync")
    }

    @Test func non2xxSurfacesStatusCode() async throws {
        StubURLProtocol.handler = { _ in (401, Data("nope".utf8)) }
        await #expect(throws: LiveAPIError.http(status: 401, body: "nope")) {
            _ = try await makeClient().ticket(sessionId: "x")
        }
    }
}
