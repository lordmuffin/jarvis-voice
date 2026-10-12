import Foundation
import Testing
@testable import JarvisLiveKit

@Suite("ServerChannel")
struct ServerChannelTests {
    @Test func fromTheReportedEnvironment() {
        #expect(ServerChannel(environment: "live") == .live)
        #expect(ServerChannel(environment: "lab") == .lab)
        #expect(ServerChannel(environment: " LAB ") == .lab)
        #expect(ServerChannel(environment: "dev") == .other("dev"))
    }

    @Test func guessedFromTheAddress() {
        #expect(ServerChannel(guessingFrom: URL(string: "https://live.lab.apj.dev")!) == .lab)
        #expect(ServerChannel(guessingFrom: URL(string: "https://LIVE.LAB.apj.dev/")!) == .lab)
        #expect(ServerChannel(guessingFrom: URL(string: "https://live.apj.dev")!) == .live)
        // "lab" has to be a whole label, not part of one.
        #expect(ServerChannel(guessingFrom: URL(string: "https://labrador.example")!) == .live)
        #expect(ServerChannel(guessingFrom: URL(string: "http://localhost:8000")!) == .live)
    }

    @Test func tags() {
        #expect(ServerChannel.live.tag == "LIVE")
        #expect(ServerChannel.lab.tag == "LAB")
        #expect(ServerChannel.other("staging").tag == "STAGIN", "kept short enough for the menu bar")
    }
}

/// Answers `/healthz` from `reply`; its own stub so this suite can run beside the others.
private final class ServerInfoStub: URLProtocol, @unchecked Sendable {
    nonisolated(unsafe) static var reply: (status: Int, body: String)?

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        guard request.url?.path == "/healthz", let reply = Self.reply else {
            client?.urlProtocol(self, didFailWithError: URLError(.cannotConnectToHost))
            return
        }
        let response = HTTPURLResponse(url: request.url!, statusCode: reply.status, httpVersion: "HTTP/1.1", headerFields: nil)!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Data(reply.body.utf8))
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}
}

@Suite("ServerInfo", .serialized)
struct ServerInfoTests {
    private let base = URL(string: "https://live.example.test")!

    private var session: URLSession {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [ServerInfoStub.self]
        return URLSession(configuration: configuration)
    }

    @Test func readsChannelAndVersionFromHealthz() async {
        ServerInfoStub.reply = (200, #"{"status":"ok","environment":"lab","version":"0.12.0"}"#)
        let info = await ServerInfo.fetch(baseURL: base, session: session)
        #expect(info == ServerInfo(channel: .lab, version: "0.12.0"))
    }

    @Test func anUnknownVersionIsNone() async {
        ServerInfoStub.reply = (200, #"{"status":"ok","environment":"live","version":"unknown"}"#)
        #expect(await ServerInfo.fetch(baseURL: base, session: session) == ServerInfo(channel: .live, version: nil))
    }

    @Test func nothingWithoutAnEnvironment() async {
        // An older server, a captive portal, a proxy error, then nothing listening at all.
        for reply in [
            (200, #"{"status":"ok"}"#),
            (200, #"{"status":"ok","environment":""}"#),
            (200, "<html>Sign in to Wi-Fi</html>"),
            (502, #"{"status":"ok","environment":"live"}"#),
        ] {
            ServerInfoStub.reply = reply
            #expect(await ServerInfo.fetch(baseURL: base, session: session) == nil)
        }
        ServerInfoStub.reply = nil
        #expect(await ServerInfo.fetch(baseURL: base, session: session) == nil)
    }
}
