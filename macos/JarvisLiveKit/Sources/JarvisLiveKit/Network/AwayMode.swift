import Foundation

/// Where this Mac is, as far as reaching Jarvis Live goes.
public enum NetworkPlace: String, Codable, Sendable {
    /// The server answered its health check: on the home network.
    case home
    /// A NetBird tunnel is up, so the server is reachable from anywhere.
    case netbird
    /// Neither: sessions should record locally.
    case away
}

/// One network interface as the system reports it.
public struct NetInterface: Equatable, Sendable {
    public var name: String
    public var isTunnel: Bool
    public var addresses: [String]

    public init(name: String, isTunnel: Bool, addresses: [String]) {
        self.name = name
        self.isTunnel = isTunnel
        self.addresses = addresses
    }
}

/// Recognizes a NetBird tunnel from the interfaces alone, without asking the server.
public enum NetBird {
    /// NetBird's default overlay range, 100.64.0.0/10.
    public static func isOverlayAddress(_ address: String) -> Bool {
        let parts = address.split(separator: ".", omittingEmptySubsequences: false)
        guard parts.count == 4 else { return false }
        var octets: [Int] = []
        for part in parts {
            guard let octet = Int(part), (0...255).contains(octet) else { return false }
            octets.append(octet)
        }
        return octets[0] == 100 && (64...127).contains(octets[1])
    }

    /// A tunnel with an address in the overlay range. ISPs and phone hotspots hand out CGNAT
    /// addresses from the same range, hence the tunnel requirement.
    public static func detect(_ interfaces: [NetInterface]) -> Bool {
        interfaces.contains { $0.isTunnel && $0.addresses.contains(where: isOverlayAddress) }
    }
}

/// Whether a new session records locally. Follows `place` (Local only when `.away`) unless the
/// user flipped the toggle. A flip holds until the automatic choice changes: turn Local only off
/// at a café because NetBird is up but went unnoticed, and it comes back on by itself the next
/// time you leave home.
public struct AutoLocalOnly: Codable, Equatable, Sendable {
    /// Settings › "Local only away from home". Off: the toggle is purely manual.
    public var enabled: Bool
    /// The last network check; nil until the first one finishes.
    public var place: NetworkPlace?
    /// The user's choice, if they made one.
    public var manual: Bool?
    /// The automatic value `manual` overrode; nil if it was not known yet.
    public var manualAgainst: Bool?

    public init(enabled: Bool = true, place: NetworkPlace? = nil, manual: Bool? = nil, manualAgainst: Bool? = nil) {
        self.enabled = enabled
        self.place = place
        self.manual = manual
        self.manualAgainst = manualAgainst
    }

    /// What the network says, or nil before the first check.
    public var automatic: Bool? { place.map { $0 == .away } }

    public var localOnly: Bool { manual ?? (enabled && automatic == true) }

    public func withPlace(_ place: NetworkPlace) -> AutoLocalOnly {
        var next = self
        next.place = place
        guard manual != nil, enabled else { return next }
        if manualAgainst == nil {
            next.manualAgainst = next.automatic
            return next.settled()
        }
        if manualAgainst == next.automatic { return next }
        next.manual = nil
        next.manualAgainst = nil
        return next
    }

    public func withManual(_ localOnly: Bool) -> AutoLocalOnly {
        var next = self
        next.manual = localOnly
        next.manualAgainst = enabled ? automatic : nil
        return enabled ? next.settled() : next
    }

    /// Turning automation off keeps what the toggle shows; turning it on hands it back.
    public func withEnabled(_ enabled: Bool) -> AutoLocalOnly {
        guard enabled != self.enabled else { return self }
        var next = self
        next.enabled = enabled
        next.manual = enabled ? nil : localOnly
        next.manualAgainst = nil
        return next
    }

    /// A choice equal to the automatic one is no override.
    private func settled() -> AutoLocalOnly {
        guard manualAgainst != nil, manual == manualAgainst else { return self }
        var next = self
        next.manual = nil
        next.manualAgainst = nil
        return next
    }
}
