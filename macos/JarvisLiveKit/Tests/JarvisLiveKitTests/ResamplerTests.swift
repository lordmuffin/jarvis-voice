import AVFoundation
import Foundation
import Testing
@testable import JarvisLiveKit

@Suite("Resampler")
struct ResamplerTests {
    /// A non-interleaved 48 kHz stereo float sine (same signal on both channels).
    private func sineBuffer(
        seconds: Double, frequency: Double = 440, amplitude: Float = 0.5,
        sampleRate: Double = 48_000
    ) throws -> AVAudioPCMBuffer {
        let format = try #require(AVAudioFormat(
            commonFormat: .pcmFormatFloat32, sampleRate: sampleRate, channels: 2, interleaved: false
        ))
        let frames = AVAudioFrameCount(seconds * sampleRate)
        let buffer = try #require(AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frames))
        buffer.frameLength = frames
        let channels = try #require(buffer.floatChannelData)
        for i in 0..<Int(frames) {
            let value = amplitude * Float(sin(2 * Double.pi * frequency * Double(i) / sampleRate))
            channels[0][i] = value
            channels[1][i] = value
        }
        return buffer
    }

    private func samples(_ frames: [Data]) -> [Int16] {
        let all = frames.reduce(into: Data()) { $0.append($1) }
        return all.withUnsafeBytes { Array($0.bindMemory(to: Int16.self)) }
    }

    @Test func convertsOneSecondOf48kStereoToHundredMsFrames() throws {
        let resampler = Resampler()
        var frames = try resampler.convert(sineBuffer(seconds: 1))
        frames += try resampler.finish()

        // 1 s @ 16 kHz mono Int16 = 32 000 bytes; the SRC's group delay may shave a few samples.
        let total = frames.reduce(0) { $0 + $1.count }
        #expect(abs(total - 32_000) <= 2 * 3200, "got \(total) bytes")
        #expect(frames.count >= 9 && frames.count <= 11)
        for frame in frames.dropLast() { #expect(frame.count == Resampler.frameBytes) }
        #expect(frames.allSatisfy { AudioFrame.isValidPayloadSize($0.count) })
    }

    @Test func preservesAmplitudeAndFrequency() throws {
        let resampler = Resampler()
        var frames = try resampler.convert(sineBuffer(seconds: 1, frequency: 440, amplitude: 0.5))
        frames += try resampler.finish()
        let pcm = samples(frames)
        let peak = pcm.map { abs(Int($0)) }.max() ?? 0

        // 0.5 full scale → ~16 384. Allow ±5% for the SRC's passband ripple.
        #expect(abs(peak - 16_384) < 820, "peak \(peak)")

        // 440 Hz → ~880 sign changes per second of audio.
        var crossings = 0
        for i in 1..<pcm.count where (pcm[i - 1] < 0) != (pcm[i] < 0) { crossings += 1 }
        let measured = Double(crossings) / 2 / (Double(pcm.count) / 16_000)
        #expect(abs(measured - 440) < 15, "measured \(measured) Hz")
    }

    @Test func streamsAcrossBuffersAndCarriesRemainders() throws {
        let resampler = Resampler()
        var frames: [Data] = []
        // 30 × 70 ms: each call is shorter than a 100 ms frame.
        for _ in 0..<30 { frames += try resampler.convert(sineBuffer(seconds: 0.07)) }
        frames += try resampler.finish()

        let total = frames.reduce(0) { $0 + $1.count }
        #expect(abs(total - Int(2.1 * 32_000)) <= 3200, "got \(total)")
        #expect(frames.dropLast().allSatisfy { $0.count == Resampler.frameBytes })
    }

    @Test func handlesDifferentInputRates() throws {
        for rate in [44_100.0, 16_000.0, 24_000.0] {
            let resampler = Resampler()
            var frames = try resampler.convert(sineBuffer(seconds: 0.5, sampleRate: rate))
            frames += try resampler.finish()
            let total = frames.reduce(0) { $0 + $1.count }
            #expect(abs(total - 16_000) <= 2 * 3200, "rate \(rate): \(total) bytes")
        }
    }

    @Test func shortTailIsPaddedToProtocolMinimum() throws {
        let resampler = Resampler()
        // 110 ms → one full frame + a ~10 ms tail that must be padded to 20 ms.
        var frames = try resampler.convert(sineBuffer(seconds: 0.11))
        frames += try resampler.finish()
        #expect(frames.allSatisfy { AudioFrame.isValidPayloadSize($0.count) })
        #expect(frames.last?.count == AudioFrame.minPayloadBytes || frames.last?.count == Resampler.frameBytes)
    }
}
