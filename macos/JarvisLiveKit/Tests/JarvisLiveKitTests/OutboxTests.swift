import Foundation
import Testing
@testable import JarvisLiveKit

@Suite("Outbox")
struct OutboxTests {
    private func makeRoot() throws -> URL {
        let root = FileManager.default.temporaryDirectory
            .appending(path: "jarvislive-outbox-\(UUID().uuidString)", directoryHint: .isDirectory)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        return root
    }

    private func payload(seq: UInt32, bytes: Int = 3200) -> Data {
        Data((0..<bytes).map { UInt8(truncatingIfNeeded: Int(seq) &* 31 &+ $0) })
    }

    private func frame(_ channel: Channel, _ seq: UInt32, bytes: Int = 3200) -> AudioFrame {
        AudioFrame(channel: channel, seq: seq, tMs: seq * 100, payload: payload(seq: seq, bytes: bytes))
    }

    @Test func appendAndReadBack() async throws {
        let root = try makeRoot()
        defer { try? FileManager.default.removeItem(at: root) }
        let outbox = try Outbox(sessionId: "s1", root: root)

        for seq in 0..<5 { try await outbox.append(frame(.mic, UInt32(seq))) }
        try await outbox.append(frame(.system, 0))

        let mic = try await outbox.unacked(.mic)
        #expect(mic.map(\.seq) == [0, 1, 2, 3, 4])
        #expect(mic[3] == frame(.mic, 3))
        #expect(try await outbox.unacked(.system).count == 1)
        #expect(await outbox.nextSeq(for: .mic) == 5)
        #expect(await outbox.unackedDurationMs(.mic) == 500)
    }

    @Test func layoutOnDisk() async throws {
        let root = try makeRoot()
        defer { try? FileManager.default.removeItem(at: root) }
        let outbox = try Outbox(sessionId: "abc", root: root)
        try await outbox.append(frame(.mic, 0))
        try await outbox.markAcked(.mic, seq: 0)

        let dir = root.appending(path: "abc")
        #expect(FileManager.default.fileExists(atPath: dir.appending(path: "mic.pcm").path))
        #expect(FileManager.default.fileExists(atPath: dir.appending(path: "acked.json").path))
        let index = try String(contentsOf: dir.appending(path: "mic.index.jsonl"), encoding: .utf8)
        let line = try #require(index.split(separator: "\n").first)
        let object = try #require(try JSONSerialization.jsonObject(with: Data(line.utf8)) as? [String: Int])
        #expect(object == ["seq": 0, "t_ms": 0, "offset": 0, "len": 3200])
        let acked = try JSONDecoder().decode(ChannelSeqs.self, from: Data(contentsOf: dir.appending(path: "acked.json")))
        #expect(acked == ChannelSeqs(mic: 0, system: nil))
    }

    @Test func unackedFiltersByAckAndFrom() async throws {
        let root = try makeRoot()
        defer { try? FileManager.default.removeItem(at: root) }
        let outbox = try Outbox(sessionId: "s", root: root)
        for seq in 0..<10 { try await outbox.append(frame(.mic, UInt32(seq))) }

        try await outbox.markAcked(.mic, seq: 3)
        #expect(try await outbox.unacked(.mic).map(\.seq) == [4, 5, 6, 7, 8, 9])
        #expect(try await outbox.unacked(.mic, from: 7).map(\.seq) == [7, 8, 9])
        #expect(try await outbox.unacked(.mic, from: 0).map(\.seq).first == 4)  // never below ack
        #expect(try await outbox.unacked(.mic, limit: 2).map(\.seq) == [4, 5])
        #expect(await outbox.unackedDurationMs(.mic, through: 5) == 200)

        // Acks are cumulative and monotonic.
        try await outbox.markAcked(.mic, seq: 1)
        #expect(await outbox.ackedSeq(for: .mic) == 3)
    }

    @Test func crashRecoveryKeepsUnackedFramesIntact() async throws {
        let root = try makeRoot()
        defer { try? FileManager.default.removeItem(at: root) }

        do {
            let outbox = try Outbox(sessionId: "crash", root: root)
            for seq in 0..<8 { try await outbox.append(frame(.mic, UInt32(seq))) }
            for seq in 0..<3 { try await outbox.append(frame(.system, UInt32(seq))) }
            try await outbox.markAcked(.mic, seq: 2)
            // `outbox` is dropped here without close() — as in a crash.
        }

        let reopened = try Outbox.reopen(sessionId: "crash", root: root)
        let mic = try await reopened.unacked(.mic)
        #expect(mic == (3..<8).map { frame(.mic, UInt32($0)) })
        #expect(try await reopened.unacked(.system) == (0..<3).map { frame(.system, UInt32($0)) })
        #expect(await reopened.ackedSeq(for: .mic) == 2)
        #expect(await reopened.ackedSeq(for: .system) == nil)
        #expect(await reopened.nextSeq(for: .mic) == 8)

        // Appending after recovery continues the sequence.
        try await reopened.append(frame(.mic, 8))
        #expect(try await reopened.unacked(.mic).map(\.seq) == [3, 4, 5, 6, 7, 8])
    }

    @Test func recoversFromTornWrites() async throws {
        let root = try makeRoot()
        defer { try? FileManager.default.removeItem(at: root) }
        do {
            let outbox = try Outbox(sessionId: "torn", root: root)
            for seq in 0..<4 { try await outbox.append(frame(.mic, UInt32(seq))) }
        }
        // Simulate a crash mid-append: payload bytes without an index line, then a half-written line.
        let dir = root.appending(path: "torn")
        let pcm = try FileHandle(forWritingTo: dir.appending(path: "mic.pcm"))
        try pcm.seekToEnd()
        try pcm.write(contentsOf: Data(repeating: 0xEE, count: 1500))
        try pcm.close()
        let index = try FileHandle(forWritingTo: dir.appending(path: "mic.index.jsonl"))
        try index.seekToEnd()
        try index.write(contentsOf: Data(#"{"seq":4,"t_ms":4"#.utf8))
        try index.close()

        let outbox = try Outbox.reopen(sessionId: "torn", root: root)
        #expect(try await outbox.unacked(.mic) == (0..<4).map { frame(.mic, UInt32($0)) })
        #expect(await outbox.nextSeq(for: .mic) == 4)

        try await outbox.append(frame(.mic, 4))
        #expect(try await outbox.unacked(.mic).last == frame(.mic, 4))
        // And it survives another reopen cleanly.
        let again = try Outbox.reopen(sessionId: "torn", root: root)
        #expect(try await again.unacked(.mic).count == 5)
    }

    @Test func rejectsNonContiguousSeqAndBadPayloads() async throws {
        let root = try makeRoot()
        defer { try? FileManager.default.removeItem(at: root) }
        let outbox = try Outbox(sessionId: "s", root: root)
        await #expect(throws: OutboxError.unexpectedSeq(channel: .mic, expected: 0, got: 3)) {
            try await outbox.append(frame(.mic, 3))
        }
        await #expect(throws: OutboxError.invalidPayloadSize(100)) {
            try await outbox.append(frame(.mic, 0, bytes: 100))
        }
    }

    @Test func reopenOfUnknownSessionThrows() async throws {
        let root = try makeRoot()
        defer { try? FileManager.default.removeItem(at: root) }
        #expect(throws: OutboxError.sessionNotFound("nope")) {
            try Outbox.reopen(sessionId: "nope", root: root)
        }
    }

    @Test func fullyAckedChannelIsCompactedAndSequenceContinues() async throws {
        let root = try makeRoot()
        defer { try? FileManager.default.removeItem(at: root) }
        let outbox = try Outbox(sessionId: "big", root: root)
        let count = 700  // 700 × 6400 B ≈ 4.3 MB, above the 4 MiB threshold
        for seq in 0..<count { try await outbox.append(frame(.mic, UInt32(seq), bytes: 6400)) }

        let pcmURL = root.appending(path: "big/mic.pcm")
        #expect((try FileManager.default.attributesOfItem(atPath: pcmURL.path)[.size] as? UInt64) ?? 0 > 4_000_000)

        try await outbox.markAcked(.mic, seq: UInt32(count - 1))
        #expect((try FileManager.default.attributesOfItem(atPath: pcmURL.path)[.size] as? UInt64) == 0)
        #expect(try await outbox.unacked(.mic).isEmpty)
        #expect(await outbox.nextSeq(for: .mic) == UInt32(count))

        try await outbox.append(frame(.mic, UInt32(count)))
        let reopened = try Outbox.reopen(sessionId: "big", root: root)
        #expect(try await reopened.unacked(.mic) == [frame(.mic, UInt32(count))])
        #expect(await reopened.nextSeq(for: .mic) == UInt32(count + 1))
    }
}
