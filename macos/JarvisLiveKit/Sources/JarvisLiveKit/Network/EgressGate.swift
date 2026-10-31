import Foundation

/// A request was refused because a local-only session is recording.
public struct EgressBlocked: Error, Equatable, Sendable, LocalizedError {
    public init() {}

    public var errorDescription: String? {
        "Blocked: a local-only session is recording, so nothing leaves this Mac."
    }
}

/// The one place that decides whether this app may talk to the network. While a local-only
/// session records, every request the app makes is refused, whatever asks, so a new feature
/// cannot leak by forgetting to check. Everything that reaches the server asks it first
/// (`LiveAPIClient`, `LiveConnection`, `NetworkPlaceDetector`, `ServerInfo`), and
/// `scripts/lint/check_egress.py` fails CI on any other way of opening a connection.
public final class EgressGate: @unchecked Sendable {
    public static let shared = EgressGate()

    private let lock = NSLock()
    private var localSessions = 0

    public init() {}

    /// A local-only session is recording.
    public var isBlocking: Bool {
        lock.lock()
        defer { lock.unlock() }
        return localSessions > 0
    }

    public func beginLocalSession() {
        lock.lock()
        defer { lock.unlock() }
        localSessions += 1
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
}
