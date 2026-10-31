import Foundation

/// A request was refused because a local-only session is recording.
public struct EgressBlocked: Error, Equatable, Sendable, LocalizedError {
    public init() {}

    public var errorDescription: String? {
        "Blocked: a local-only session is recording, so nothing leaves this Mac."
    }
}

/// The one place that decides whether this app may talk to the network. While a local-only
/// session records, whatever goes through a `GatedSession` is refused, and a `LiveConnection`
/// already open is cut through `whenShut`. Everything that reaches the server holds a
/// `GatedSession` rather than a `URLSession` (`LiveAPIClient`, `LiveConnection`,
/// `NetworkPlaceDetector`, `ServerInfo`), and `scripts/lint/check_egress.py` fails CI on the usual
/// ways of opening a connection anywhere else.
public final class EgressGate: @unchecked Sendable {
    public static let shared = EgressGate()

    private let lock = NSLock()
    private var localSessions = 0
    private var onShut: [UUID: @Sendable () -> Void] = [:]

    public init() {}

    /// A local-only session is recording.
    public var isBlocking: Bool {
        lock.lock()
        defer { lock.unlock() }
        return localSessions > 0
    }

    public func beginLocalSession() {
        lock.lock()
        localSessions += 1
        let actions = localSessions == 1 ? Array(onShut.values) : []
        lock.unlock()
        actions.forEach { $0() }
    }

    public func endLocalSession() {
        lock.lock()
        defer { lock.unlock() }
        localSessions = max(0, localSessions - 1)
    }

    /// Throws `EgressBlocked` while a local-only session records.
    public func check() throws {
        if isBlocking { throw EgressBlocked() }
    }

    /// Runs `action` each time the gate shuts, on the caller's thread. Returns what removes it.
    @discardableResult
    public func whenShut(_ action: @escaping @Sendable () -> Void) -> @Sendable () -> Void {
        let id = UUID()
        lock.lock()
        defer { lock.unlock() }
        onShut[id] = action
        return { [weak self] in self?.remove(id) }
    }

    private func remove(_ id: UUID) {
        lock.lock()
        defer { lock.unlock() }
        onShut[id] = nil
    }
}

/// A `URLSession` that can only be used through its gate, checked at the moment of sending. Hold
/// this rather than the session, so a new request cannot skip the check.
public struct GatedSession: Sendable {
    private let session: URLSession
    private let gate: EgressGate

    public init(_ session: URLSession, gate: EgressGate = .shared) {
        self.session = session
        self.gate = gate
    }

    public func data(for request: URLRequest) async throws -> (Data, URLResponse) {
        try gate.check()
        return try await session.data(for: request)
    }

    public func webSocketTask(with url: URL) throws -> URLSessionWebSocketTask {
        try gate.check()
        return session.webSocketTask(with: url)
    }
}
