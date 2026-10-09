import Foundation
import Testing
@testable import JarvisLiveKit

/// Polls `condition` until it is true or `timeout` elapses.
private func eventually(
    timeout: Duration = .seconds(10),
    _ condition: @Sendable () async throws -> Bool
) async rethrows -> Bool {
    let deadline = ContinuousClock.now + timeout
    while ContinuousClock.now < deadline {
        if try await condition() { return true }
        try? await Task.sleep(for: .milliseconds(20))
    }
    return try await condition()
}

private func tone(_ seed: Int, bytes: Int = 3200) -> Data {
    Data((0..<bytes).map { UInt8(truncatingIfNeeded: seed &* 7 &+ $0) })
}

private struct Harness {
    let server: TestWebSocketServer
    let outbox: Outbox
    let connection: LiveConnection
    let root: URL
    let ticketCalls: Mutex<Int>

    static func make(
        maxUnackedAudioMs: Int = 5_000
    ) async throws -> Harness {
        let server = TestWebSocketServer()
        try await server.start()
        let root = FileManager.default.temporaryDirectory
            .appending(path: "jarvislive-transport-\(UUID().uuidString)", directoryHint: .isDirectory)
        let outbox = try Outbox(sessionId: "sess", root: root)
        let calls = Mutex(0)
        let configuration = LiveConnection.Configuration(
            apiBaseURL: server.baseURL,
            sessionId: "3f2b8c1e-5d4a-4b7e-9a10-2c6d8e9f0a1b",
            device: "test-mac",
            maxUnackedAudioMs: maxUnackedAudioMs,
            reconnect: ReconnectPolicy(initialDelay: 0.05, maxDelay: 0.2, jitterFraction: 0),
            handshakeTimeout: .seconds(5),
            endGracePeriod: .seconds(3)
        )
        let connection = LiveConnection(
            configuration: configuration,
            outbox: outbox,
            ticketProvider: {
                calls.withLock { $0 += 1 }
                return "tkt_test"
            }
        )
        return Harness(server: server, outbox: outbox, connection: connection, root: root, ticketCalls: calls)
    }

    func teardown() async {
        await connection.stop()
        server.stop()
        try? FileManager.default.removeItem(at: root)
    }
}

@Suite("LiveConnection", .serialized)
struct LiveConnectionTests {
    @Test func handshakeSendsHelloAndSurfacesServerEvents() async throws {
        let h = try await Harness.make()
        defer { Task { await h.teardown() } }

        await h.connection.start()
        #expect(await eventually { h.server.state().hellos.count == 1 })

        let hello = try #require(h.server.state().hellos.first)
        #expect(hello.device == "test-mac")
        #expect(hello.resume == ChannelSeqs(mic: nil, system: nil))

        #expect(await eventually { await h.connection.state == .connected })

        h.server.push(.segment(Segment(
            id: "seg_1", channel: .system, speaker: .them, startMs: 0, endMs: 900,
            text: "Hello everyone", sttTier: "local"
        )))
        var iterator = h.connection.events.makeAsyncIterator()
        let event = await iterator.next()
        guard case .segment(let segment)? = event else {
            Issue.record("expected segment, got \(String(describing: event))")
            return
        }
        #expect(segment.text == "Hello everyone")
    }

    @Test func acksAdvanceTheOutbox() async throws {
        let h = try await Harness.make()
        defer { Task { await h.teardown() } }
        await h.connection.start()

        for i in 0..<10 {
            try await h.connection.enqueueAudio(channel: .mic, payload: tone(i), tMs: UInt32(i * 100))
        }
        #expect(await eventually { await h.outbox.ackedSeq(for: .mic) == 9 })
        #expect(await h.outbox.unackedDurationMs(.mic) == 0)
        #expect(try await h.outbox.unacked(.mic).isEmpty)

        let server = h.server.state()
        #expect(server.received[.mic] == Array(0..<10))
        #expect(server.acked.mic == 9)
        #expect(server.duplicates == 0)
    }

    @Test func bothChannelsAreStreamedAndAcked() async throws {
        let h = try await Harness.make()
        defer { Task { await h.teardown() } }
        await h.connection.start()
        for i in 0..<5 {
            try await h.connection.enqueueAudio(channel: .mic, payload: tone(i), tMs: UInt32(i * 100))
            try await h.connection.enqueueAudio(channel: .system, payload: tone(i + 50), tMs: UInt32(i * 100))
        }
        #expect(await eventually {
            let a = await h.outbox.ackedSeqs()
            return a == ChannelSeqs(mic: 4, system: 4)
        })
    }

    @Test func forcedDisconnectReconnectsAndResumesWithoutDuplicates() async throws {
        let h = try await Harness.make()
        defer { Task { await h.teardown() } }
        await h.connection.start()

        for i in 0..<30 {
            try await h.connection.enqueueAudio(channel: .mic, payload: tone(i), tMs: UInt32(i * 100))
        }
        #expect(await eventually { (h.server.state().received[.mic]?.count ?? 0) >= 10 })

        h.server.dropConnections()

        for i in 30..<60 {
            try await h.connection.enqueueAudio(channel: .mic, payload: tone(i), tMs: UInt32(i * 100))
            try? await Task.sleep(for: .milliseconds(5))
        }

        #expect(await eventually { await h.outbox.ackedSeq(for: .mic) == 59 })

        let server = h.server.state()
        #expect(server.connectionCount >= 2)
        #expect(server.hellos.count >= 2)
        // A fresh (60 s, single-use) ticket is fetched for every connection attempt.
        #expect(h.ticketCalls.withLock { $0 } >= server.connectionCount)
        #expect(server.duplicates == 0, "server saw \(server.duplicates) duplicate frames")
        #expect(server.gaps == 0)
        #expect(server.received[.mic] == Array(0..<60), "frames must arrive exactly once, in order")

        // The second hello resumes from what the client had acked, never from the future.
        let resumed = try #require(server.hellos.last?.resume.mic)
        #expect(resumed <= 59)
        #expect(try await h.outbox.unacked(.mic).isEmpty)
    }

    @Test func resumesAfterAppRestartFromDurableOutbox() async throws {
        let h = try await Harness.make()
        defer { Task { await h.teardown() } }

        // Capture while offline (connection never started): frames live only in the outbox.
        for i in 0..<12 {
            try await h.outbox.append(channel: .mic, payload: tone(i), tMs: UInt32(i * 100))
        }
        await h.connection.start()
        await h.connection.kick()
        #expect(await eventually { await h.outbox.ackedSeq(for: .mic) == 11 })
        #expect(h.server.state().received[.mic] == Array(0..<12))
    }

    @Test func backpressurePausesWhileTooMuchAudioIsUnacked() async throws {
        let h = try await Harness.make(maxUnackedAudioMs: 1_000)
        defer { Task { await h.teardown() } }
        h.server.setHoldAcks(true)
        await h.connection.start()
        #expect(await eventually { await h.connection.state == .connected })

        // 3 s of audio in 100 ms frames.
        for i in 0..<30 {
            try await h.connection.enqueueAudio(channel: .mic, payload: tone(i), tMs: UInt32(i * 100))
        }
        #expect(await eventually { (h.server.state().received[.mic]?.count ?? 0) >= 10 })
        try await Task.sleep(for: .milliseconds(300))

        let stalled = h.server.state().received[.mic]?.count ?? 0
        #expect(stalled >= 10 && stalled <= 12, "sent \(stalled) frames without acks; limit is ~10")
        // Nothing was dropped locally: everything is still in the outbox.
        #expect(try await h.outbox.unacked(.mic).count == 30)

        h.server.setHoldAcks(false)
        #expect(await eventually { await h.outbox.ackedSeq(for: .mic) == 29 })
        #expect(h.server.state().received[.mic] == Array(0..<30))
    }

    @Test func draftsAndMarkersAreDelivered() async throws {
        let h = try await Harness.make()
        defer { Task { await h.teardown() } }
        await h.connection.start()
        #expect(await eventually { await h.connection.state == .connected })

        let draft = DraftSegment(channel: .mic, startMs: 0, endMs: 800, text: "hi there", final: false)
        await h.connection.sendDraft(draft)
        await h.connection.sendMarker(Marker(tMs: 5000, label: "decision"))

        #expect(await eventually {
            let s = h.server.state()
            return s.drafts == [draft] && s.markers == [Marker(tMs: 5000, label: "decision")]
        })
    }

    @Test func endFlushesAudioSendsEndAndFinishes() async throws {
        let h = try await Harness.make()
        defer { Task { await h.teardown() } }
        await h.connection.start()

        for i in 0..<8 {
            try await h.connection.enqueueAudio(channel: .mic, payload: tone(i), tMs: UInt32(i * 100))
        }
        await h.connection.end()

        let server = h.server.state()
        #expect(server.endReceived)
        #expect(server.received[.mic] == Array(0..<8), "all audio must precede end")
        #expect(await h.outbox.unackedDurationMs(.mic) == 0)
        #expect(await h.connection.state == .ended)

        var sawFinalNote = false
        for await event in h.connection.events {
            if case .finalNote(let note) = event { sawFinalNote = note.title == "Test" }
        }
        #expect(sawFinalNote)
    }
}
