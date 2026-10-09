import Foundation

public enum LiveAPIError: Error, Equatable, Sendable {
    case http(status: Int, body: String)
    case invalidResponse
}

/// Minimal REST client for session creation and WebSocket tickets.
public struct LiveAPIClient: Sendable {
    public let baseURL: URL
    private let session: URLSession
    private let bearerToken: @Sendable () async throws -> String

    public init(
        baseURL: URL,
        session: URLSession = .shared,
        bearerToken: @escaping @Sendable () async throws -> String
    ) {
        self.baseURL = baseURL
        self.session = session
        self.bearerToken = bearerToken
    }

    /// `POST /v1/sessions`
    public func createSession(_ request: SessionCreateRequest) async throws -> SessionCreateResponse {
        try await post("v1/sessions", body: request)
    }

    /// `POST /v1/sessions/{id}/ticket`
    public func ticket(sessionId: String, role: TicketRole = .producer) async throws -> TicketResponse {
        try await post("v1/sessions/\(sessionId)/ticket", body: TicketRequest(role: role))
    }

    /// A `ticketProvider` suitable for `LiveConnection`.
    public func producerTicketProvider(sessionId: String) -> @Sendable () async throws -> String {
        { try await self.ticket(sessionId: sessionId, role: .producer).ticket }
    }

    private func post<Body: Encodable, Response: Decodable>(_ path: String, body: Body) async throws -> Response {
        var request = URLRequest(url: baseURL.appending(path: path))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("Bearer \(try await bearerToken())", forHTTPHeaderField: "Authorization")
        request.httpBody = try JSONEncoder().encode(body)

        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw LiveAPIError.invalidResponse }
        guard (200..<300).contains(http.statusCode) else {
            throw LiveAPIError.http(status: http.statusCode, body: String(decoding: data.prefix(512), as: UTF8.self))
        }
        return try JSONDecoder().decode(Response.self, from: data)
    }
}
