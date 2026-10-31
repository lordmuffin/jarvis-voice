import Foundation

/// Which Jarvis Live release channel a server is: `live` runs the latest release, `lab` the
/// latest commit on main (see `server/deploy/README.md`).
public enum ServerChannel: Equatable, Sendable {
    case live
    case lab
    /// Any other `JARVIS_LIVE_ENVIRONMENT`, such as a dev server's.
    case other(String)

    /// From the `environment` the server reports in `GET /healthz`.
    public init(environment: String) {
        switch environment.trimmingCharacters(in: .whitespaces).lowercased() {
        case "live": self = .live
        case "lab": self = .lab
        case let name: self = .other(name)
        }
    }

    /// A guess from the address alone, for before the server answers or while it can't be
    /// reached. Lab's host has a `lab` label (`live.lab.apj.dev`); anything else is taken as live.
    public init(guessingFrom url: URL) {
        let labels = (url.host() ?? "").lowercased().split(separator: ".")
        self = labels.contains("lab") ? .lab : .live
    }

    /// The short tag shown beside the menu-bar icon and in Settings.
    public var tag: String {
        switch self {
        case .live: "LIVE"
        case .lab: "LAB"
        case .other(let name): String(name.uppercased().prefix(6))
        }
    }
}

/// What a server says about itself in `GET /healthz`.
public struct ServerInfo: Equatable, Sendable {
    public var channel: ServerChannel
    /// The server's release version; nil when it doesn't know its own.
    public var version: String?

    public init(channel: ServerChannel, version: String?) {
        self.channel = channel
        self.version = version
    }

    /// Asks the server. Nil when it can't be reached, answers with anything but an ok health
    /// check, or doesn't name its environment (servers before the lab channel didn't).
    public static func fetch(baseURL: URL, session: URLSession? = nil, gate: EgressGate = .shared) async -> ServerInfo? {
        let request = URLRequest(
            url: baseURL.appending(path: "healthz"), timeoutInterval: NetworkPlaceDetector.probeTimeout
        )
        let result = try? await GatedSession(session ?? NetworkPlaceDetector.probeSession, gate: gate)
            .data(for: request)
        guard let data = result?.0, let http = result?.1 as? HTTPURLResponse, (200..<300).contains(http.statusCode)
        else { return nil }
        struct Health: Decodable {
            let status: String
            let environment: String?
            let version: String?
        }
        guard let health = try? JSONDecoder().decode(Health.self, from: data), health.status == "ok",
              let environment = health.environment, !environment.trimmingCharacters(in: .whitespaces).isEmpty
        else { return nil }
        let version = health.version.flatMap { $0.isEmpty || $0 == "unknown" ? nil : $0 }
        return ServerInfo(channel: ServerChannel(environment: environment), version: version)
    }
}
