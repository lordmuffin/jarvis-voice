import Foundation
import Testing
@testable import JarvisLiveKit

@Suite("StartFallback")
struct StartFallbackTests {
    @Test func unreachableServerFallsBackToLocalRecording() {
        for code in [URLError.notConnectedToInternet, .timedOut, .cannotFindHost, .cannotConnectToHost, .networkConnectionLost] {
            #expect(StartFallback.shouldFallBack(URLError(code)))
        }
    }

    @Test func serverTroubleFallsBack() {
        for status in [500, 502, 503, 504] {
            #expect(StartFallback.shouldFallBack(LiveAPIError.http(status: status, body: "")))
        }
        #expect(StartFallback.shouldFallBack(LiveAPIError.invalidResponse))
    }

    /// A rejected token or a missing session is not an outage: record nothing silently, tell the user.
    @Test func rejectedCredentialsStillSurface() {
        for status in [400, 401, 403, 404, 422] {
            #expect(!StartFallback.shouldFallBack(LiveAPIError.http(status: status, body: "")))
        }
    }

    @Test func otherErrorsStillSurface() {
        #expect(!StartFallback.shouldFallBack(CancellationError()))
        #expect(!StartFallback.shouldFallBack(URLError(.cancelled)))
    }
}
