import Foundation
import Testing
@testable import JarvisLiveKit

@Suite("NetBird")
struct NetBirdTests {
    @Test func overlayRangeIsNetBirdsDefault() {
        #expect(NetBird.isOverlayAddress("100.64.0.1"))
        #expect(NetBird.isOverlayAddress("100.127.255.254"))
        #expect(!NetBird.isOverlayAddress("100.63.255.255"))
        #expect(!NetBird.isOverlayAddress("100.128.0.1"))
        #expect(!NetBird.isOverlayAddress("192.168.1.10"))
        #expect(!NetBird.isOverlayAddress("fe80::1%en0"))
        #expect(!NetBird.isOverlayAddress("100.64.0"))
        #expect(!NetBird.isOverlayAddress("100.64.0.256"))
    }

    @Test func netBirdNeedsATunnel() {
        // A phone hotspot or ISP CGNAT can put a 100.64/10 address on Wi-Fi.
        let wifi = NetInterface(name: "en0", isTunnel: false, addresses: ["100.72.3.4"])
        let netbird = NetInterface(name: "utun100", isTunnel: true, addresses: ["100.92.10.5"])
        let otherVPN = NetInterface(name: "utun4", isTunnel: true, addresses: ["10.8.0.2"])
        #expect(!NetBird.detect([wifi, otherVPN]))
        #expect(NetBird.detect([wifi, netbird]))
    }

    @Test func currentInterfacesIncludeLoopback() {
        let loopback = NetworkPlaceDetector.currentInterfaces().first { $0.name == "lo0" }
        #expect(loopback?.addresses.contains("127.0.0.1") == true)
    }
}

@Suite("AutoLocalOnly")
struct AutoLocalOnlyTests {
    @Test func followsTheNetwork() {
        var mode = AutoLocalOnly()
        #expect(!mode.localOnly, "unknown until the first check")
        mode = mode.withPlace(.away)
        #expect(mode.localOnly)
        mode = mode.withPlace(.netbird)
        #expect(!mode.localOnly)
        mode = mode.withPlace(.home)
        #expect(!mode.localOnly)
    }

    @Test func manualChoiceHoldsUntilTheAutomaticOneChanges() {
        // Away, NetBird running but not seen: stream anyway.
        var mode = AutoLocalOnly().withPlace(.away).withManual(false)
        #expect(!mode.localOnly)
        #expect(mode.manual == false)
        // Still away (a re-check after a Wi-Fi hop): the choice stands.
        mode = mode.withPlace(.away)
        #expect(!mode.localOnly)
        // Home: automatic says stream too, and the choice is spent.
        mode = mode.withPlace(.home)
        #expect(mode.manual == nil)
        // Leaving again turns Local only back on.
        mode = mode.withPlace(.away)
        #expect(mode.localOnly)
    }

    @Test func manualLocalOnlyAtHomeSurvivesHomeToNetBird() {
        var mode = AutoLocalOnly().withPlace(.home).withManual(true)
        mode = mode.withPlace(.netbird)
        #expect(mode.localOnly, "both stream automatically, so nothing changed")
        mode = mode.withPlace(.away)
        #expect(mode.manual == nil)
        #expect(mode.localOnly)
        mode = mode.withPlace(.home)
        #expect(!mode.localOnly)
    }

    @Test func flippingBackToTheAutomaticValueClearsTheOverride() {
        let mode = AutoLocalOnly().withPlace(.away).withManual(false).withManual(true)
        #expect(mode.manual == nil)
        #expect(mode.localOnly)
    }

    @Test func choiceBeforeTheFirstCheckIsMeasuredAgainstIt() {
        let pending = AutoLocalOnly().withManual(false)
        #expect(pending.manualAgainst == nil)
        let away = pending.withPlace(.away)
        #expect(!away.localOnly)
        #expect(away.manualAgainst == true)
        #expect(pending.withPlace(.home).manual == nil)
    }

    @Test func disabledIsAPlainToggle() {
        var mode = AutoLocalOnly().withPlace(.away).withEnabled(false)
        #expect(mode.localOnly, "keeps what the toggle showed")
        mode = mode.withManual(false).withPlace(.home).withPlace(.away)
        #expect(!mode.localOnly)
        mode = mode.withEnabled(true)
        #expect(mode.manual == nil)
        #expect(mode.localOnly)
    }

    @Test func roundTripsThroughJSON() throws {
        let mode = AutoLocalOnly().withPlace(.away).withManual(false)
        let decoded = try JSONDecoder().decode(AutoLocalOnly.self, from: JSONEncoder().encode(mode))
        #expect(decoded == mode)
    }
}

/// Answers `/healthz` from `reply`; separate from the API client tests' stub so the suites can run in parallel.
private final class HealthStub: URLProtocol, @unchecked Sendable {
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

@Suite("NetworkPlaceDetector", .serialized)
struct NetworkPlaceDetectorTests {
    private let base = URL(string: "https://live.example.test")!
    private let wifi = [NetInterface(name: "en0", isTunnel: false, addresses: ["192.168.1.20"])]

    private var session: URLSession {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [HealthStub.self]
        return URLSession(configuration: configuration)
    }

    @Test func homeWhenHealthzSaysOk() async {
        HealthStub.reply = (200, #"{"status":"ok"}"#)
        #expect(await NetworkPlaceDetector.detect(baseURL: base, interfaces: wifi, session: session) == .home)
    }

    @Test func awayOnAnythingElse() async {
        // A captive portal's login page, a proxy error, then nothing listening at all.
        HealthStub.reply = (200, "<html>Sign in to Hotel Wi-Fi</html>")
        #expect(await NetworkPlaceDetector.detect(baseURL: base, interfaces: wifi, session: session) == .away)
        HealthStub.reply = (502, #"{"status":"ok"}"#)
        #expect(await NetworkPlaceDetector.detect(baseURL: base, interfaces: wifi, session: session) == .away)
        HealthStub.reply = nil
        #expect(await NetworkPlaceDetector.detect(baseURL: base, interfaces: wifi, session: session) == .away)
    }

    @Test func netBirdNeedsNoProbe() async {
        HealthStub.reply = nil
        let tunnel = wifi + [NetInterface(name: "utun100", isTunnel: true, addresses: ["100.92.10.5"])]
        #expect(await NetworkPlaceDetector.detect(baseURL: base, interfaces: tunnel, session: session) == .netbird)
    }
}
