import Foundation
import Testing
@testable import JarvisLiveKit

@Suite("ReconnectPolicy")
struct ReconnectPolicyTests {
    @Test func doublesFromHalfASecondAndCapsAtThirty() {
        let policy = ReconnectPolicy()
        let delays = (0..<10).map { policy.delay(attempt: $0, random: 0) }
        #expect(delays == [0.5, 1, 2, 4, 8, 16, 30, 30, 30, 30])
    }

    @Test func jitterIsAdditiveBoundedAndNeverExceedsCap() {
        let policy = ReconnectPolicy()
        #expect(policy.delay(attempt: 0, random: 1) == 0.625)
        #expect(policy.delay(attempt: 2, random: 0.5) == 2.25)
        for attempt in 0..<40 {
            for r in stride(from: 0.0, through: 1.0, by: 0.25) {
                let d = policy.delay(attempt: attempt, random: r)
                #expect(d >= 0.5 && d <= 30)
            }
        }
    }
}
