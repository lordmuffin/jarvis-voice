import Foundation

/// Works out the Mac's `NetworkPlace`: NetBird first (no round trip), then the server's health check.
public enum NetworkPlaceDetector {
    /// Away, the server's address usually times out rather than refusing; don't wait long.
    public static let probeTimeout: TimeInterval = 4

    public static func detect(
        baseURL: URL,
        interfaces: [NetInterface] = currentInterfaces(),
        session: URLSession? = nil
    ) async -> NetworkPlace {
        if NetBird.detect(interfaces) { return .netbird }
        return await isReachable(baseURL: baseURL, session: session) ? .home : .away
    }

    /// `GET /healthz`: can this network reach the server at all? Any failure is false. Checks the
    /// body so a captive portal answering 200 doesn't count.
    public static func isReachable(baseURL: URL, session: URLSession? = nil, gate: EgressGate = .shared) async -> Bool {
        guard !gate.isBlocking else { return false }
        let request = URLRequest(url: baseURL.appending(path: "healthz"), timeoutInterval: probeTimeout)
        let result = try? await (session ?? probeSession).data(for: request)
        guard let data = result?.0, let http = result?.1 as? HTTPURLResponse, (200..<300).contains(http.statusCode)
        else { return false }
        struct Health: Decodable { let status: String }
        return (try? JSONDecoder().decode(Health.self, from: data))?.status == "ok"
    }

    /// Caps the whole request, not just the gaps between packets, and never waits for a network.
    static let probeSession: URLSession = {
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = probeTimeout
        config.timeoutIntervalForResource = probeTimeout
        config.waitsForConnectivity = false
        return URLSession(configuration: config)
    }()

    /// The IPv4 addresses of every interface, grouped by interface. `utun*` and point-to-point
    /// links count as tunnels; NetBird's is `utun100` by default.
    public static func currentInterfaces() -> [NetInterface] {
        var head: UnsafeMutablePointer<ifaddrs>?
        guard getifaddrs(&head) == 0, let first = head else { return [] }
        defer { freeifaddrs(head) }
        var byName: [String: NetInterface] = [:]
        var order: [String] = []
        for pointer in sequence(first: first, next: { $0.pointee.ifa_next }) {
            let ifa = pointer.pointee
            let name = String(cString: ifa.ifa_name)
            if byName[name] == nil {
                let pointToPoint = ifa.ifa_flags & UInt32(IFF_POINTOPOINT) != 0
                byName[name] = NetInterface(name: name, isTunnel: name.hasPrefix("utun") || pointToPoint, addresses: [])
                order.append(name)
            }
            guard let addr = ifa.ifa_addr, addr.pointee.sa_family == sa_family_t(AF_INET) else { continue }
            var host = [CChar](repeating: 0, count: Int(NI_MAXHOST))
            let status = getnameinfo(
                addr, socklen_t(addr.pointee.sa_len), &host, socklen_t(host.count), nil, 0, NI_NUMERICHOST
            )
            guard status == 0 else { continue }
            byName[name]?.addresses.append(host.withUnsafeBufferPointer { String(cString: $0.baseAddress!) })
        }
        return order.compactMap { byName[$0] }
    }
}
