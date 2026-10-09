import Foundation

/// Exponential backoff with additive jitter: 0.5 s, 1 s, 2 s … capped at 30 s.
public struct ReconnectPolicy: Equatable, Sendable {
    public var initialDelay: TimeInterval
    public var maxDelay: TimeInterval
    /// Up to this fraction of the base delay is added on top, scaled by a random value in 0...1.
    public var jitterFraction: Double

    public init(initialDelay: TimeInterval = 0.5, maxDelay: TimeInterval = 30, jitterFraction: Double = 0.25) {
        self.initialDelay = initialDelay
        self.maxDelay = maxDelay
        self.jitterFraction = jitterFraction
    }

    /// - Parameters:
    ///   - attempt: zero-based retry count.
    ///   - random: a value in 0...1 (injectable for tests).
    public func delay(attempt: Int, random: Double = .random(in: 0...1)) -> TimeInterval {
        let exponent = Double(min(max(attempt, 0), 30))
        let base = min(maxDelay, initialDelay * pow(2, exponent))
        return min(maxDelay, base * (1 + jitterFraction * random))
    }
}
