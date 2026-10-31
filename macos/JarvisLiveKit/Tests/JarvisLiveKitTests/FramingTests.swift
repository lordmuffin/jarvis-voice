import Foundation
import Testing
@testable import JarvisLiveKit

@Suite("Framing")
struct FramingTests {
    private func goldenBytes() throws -> Data {
        let text = try String(contentsOf: Fixtures.root.appending(path: "frame_mic_seq7.hex"), encoding: .utf8)
        let hex = text.filter(\.isHexDigit)
        var data = Data()
        var index = hex.startIndex
        while index < hex.endIndex {
            let next = hex.index(index, offsetBy: 2)
            data.append(try #require(UInt8(hex[index..<next], radix: 16)))
            index = next
        }
        return data
    }

    @Test func decodesGoldenFrame() throws {
        let frame = try AudioFrame(decoding: goldenBytes())
        #expect(frame.channel == .mic)
        #expect(frame.seq == 7)
        #expect(frame.tMs == 140)
        #expect(frame.payload.count == 640)
        #expect(frame.durationMs == 20)
        #expect(frame.payload.allSatisfy { $0 == 0 })
    }

    @Test func encodesGoldenFrameByteForByte() throws {
        let golden = try goldenBytes()
        let frame = AudioFrame(channel: .mic, seq: 7, tMs: 140, payload: Data(count: 640))
        #expect(try frame.encoded() == golden)
    }

    @Test func headerIsLittleEndian() throws {
        let frame = AudioFrame(channel: .system, seq: 0x0102_0304, tMs: 0x0A0B_0C0D, payload: Data(count: 640))
        let bytes = [UInt8](try frame.encoded().prefix(12))
        #expect(bytes == [1, 1, 0, 0, 0x04, 0x03, 0x02, 0x01, 0x0D, 0x0C, 0x0B, 0x0A])
    }

    @Test func roundTripsSystemChannel() throws {
        let payload = Data((0..<3200).map { UInt8(truncatingIfNeeded: $0) })
        let frame = AudioFrame(channel: .system, seq: 99, tMs: 12_345, payload: payload)
        #expect(try AudioFrame(decoding: frame.encoded()) == frame)
    }

    @Test func payloadBoundsAreEnforced() throws {
        let tooSmall = AudioFrame(channel: .mic, seq: 0, tMs: 0, payload: Data(count: 638))
        let tooBig = AudioFrame(channel: .mic, seq: 0, tMs: 0, payload: Data(count: 6402))
        #expect(throws: FrameError.payloadSize(638)) { try tooSmall.encoded() }
        #expect(throws: FrameError.payloadSize(6402)) { try tooBig.encoded() }

        let min = AudioFrame(channel: .mic, seq: 0, tMs: 0, payload: Data(count: 640))
        let max = AudioFrame(channel: .mic, seq: 0, tMs: 0, payload: Data(count: 6400))
        #expect(throws: Never.self) { try min.encoded() }
        #expect(throws: Never.self) { try max.encoded() }
    }

    @Test func rejectsMalformedInput() throws {
        let good = try AudioFrame(channel: .mic, seq: 1, tMs: 0, payload: Data(count: 640)).encoded()
        #expect(throws: FrameError.tooShort(5)) { try AudioFrame(decoding: Data(count: 5)) }

        var badVersion = good
        badVersion[0] = 2
        #expect(throws: FrameError.unsupportedVersion(2)) { try AudioFrame(decoding: badVersion) }

        var badChannel = good
        badChannel[1] = 7
        #expect(throws: FrameError.unknownChannel(7)) { try AudioFrame(decoding: badChannel) }

        #expect(throws: FrameError.payloadSize(100)) {
            try AudioFrame(decoding: good.prefix(12 + 100))
        }
    }
}
