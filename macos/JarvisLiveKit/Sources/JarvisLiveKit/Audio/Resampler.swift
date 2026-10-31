import AVFoundation
import Foundation

public enum ResamplerError: Error, Sendable {
    case unsupportedFormat
    case conversionFailed(String)
}

/// Converts arbitrary `AVAudioPCMBuffer`s to 16 kHz mono Int16 little-endian PCM and slices the
/// result into fixed 100 ms frames (3200 bytes).
///
/// Stateful: remainders shorter than one frame are carried over to the next `convert` call, and the
/// sample-rate converter keeps its filter state between calls. Call `finish()` once at the end of a
/// stream to drain the converter and emit the tail.
///
/// Not thread-safe; own it from the single task/queue that handles the capture tap.
public final class Resampler {
    public static let sampleRate: Double = 16_000
    public static let frameMs = 100
    public static let frameBytes = frameMs * AudioFrame.bytesPerMs  // 3200

    private let outputFormat: AVAudioFormat
    private var converter: AVAudioConverter?
    private var converterInputFormat: AVAudioFormat?
    private var pending = Data()

    public init() {
        // Interleaved Int16 mono at 16 kHz. Force-unwrapped: the parameters are constants.
        outputFormat = AVAudioFormat(
            commonFormat: .pcmFormatInt16, sampleRate: Self.sampleRate, channels: 1, interleaved: true
        )!
    }

    /// Converts `buffer` and returns every complete 100 ms frame now available.
    public func convert(_ buffer: AVAudioPCMBuffer) throws -> [Data] {
        guard buffer.frameLength > 0 else { return [] }
        try drainIfFormatChanged(to: buffer.format)
        let converter = try converter(for: buffer.format)

        let ratio = Self.sampleRate / buffer.format.sampleRate
        let capacity = AVAudioFrameCount((Double(buffer.frameLength) * ratio).rounded(.up)) + 64
        try append(from: converter, capacity: capacity, input: buffer, endOfStream: false)
        return takeFrames()
    }

    /// Drains the converter and returns the remaining audio. A tail shorter than the protocol's
    /// 20 ms minimum is zero-padded so it can still be sent as a valid frame.
    public func finish() throws -> [Data] {
        if converter != nil { try drainConverter() }
        var frames = takeFrames()
        if !pending.isEmpty {
            if pending.count < AudioFrame.minPayloadBytes {
                pending.append(Data(count: AudioFrame.minPayloadBytes - pending.count))
            }
            frames.append(pending)
            pending = Data()
        }
        converter = nil
        converterInputFormat = nil
        return frames
    }

    // MARK: - Internals

    private func converter(for format: AVAudioFormat) throws -> AVAudioConverter {
        if let converter, converterInputFormat == format { return converter }
        guard let made = AVAudioConverter(from: format, to: outputFormat) else {
            throw ResamplerError.unsupportedFormat
        }
        converter = made
        converterInputFormat = format
        return made
    }

    private func drainIfFormatChanged(to format: AVAudioFormat) throws {
        guard let current = converterInputFormat, current != format else { return }
        try drainConverter()
        converter = nil
        converterInputFormat = nil
    }

    private func drainConverter() throws {
        guard let converter else { return }
        try append(from: converter, capacity: 4096, input: nil, endOfStream: true)
    }

    private func append(
        from converter: AVAudioConverter,
        capacity: AVAudioFrameCount,
        input: AVAudioPCMBuffer?,
        endOfStream: Bool
    ) throws {
        guard let output = AVAudioPCMBuffer(pcmFormat: outputFormat, frameCapacity: capacity) else {
            throw ResamplerError.conversionFailed("could not allocate output buffer")
        }
        let feed = InputFeed(buffer: input, endOfStream: endOfStream)
        var error: NSError?
        let status = converter.convert(to: output, error: &error) { _, outStatus in
            feed.next(outStatus)
        }
        if status == .error {
            throw ResamplerError.conversionFailed(error?.localizedDescription ?? "unknown error")
        }
        let byteCount = Int(output.frameLength) * MemoryLayout<Int16>.size
        if byteCount > 0, let src = output.audioBufferList.pointee.mBuffers.mData {
            pending.append(Data(bytes: src, count: byteCount))
        }
    }

    private func takeFrames() -> [Data] {
        var frames: [Data] = []
        while pending.count >= Self.frameBytes {
            frames.append(pending.prefix(Self.frameBytes))
            pending = Data(pending.dropFirst(Self.frameBytes))
        }
        return frames
    }
}

/// Hands a single input buffer to `AVAudioConverter`'s synchronous input block.
///
/// `AVAudioConverter` invokes the block on the calling thread before `convert` returns, so the
/// mutable state is never accessed concurrently; the box exists to satisfy the block's `@Sendable`
/// requirement.
private final class InputFeed: @unchecked Sendable {
    private var buffer: AVAudioPCMBuffer?
    private let endOfStream: Bool

    init(buffer: AVAudioPCMBuffer?, endOfStream: Bool) {
        self.buffer = buffer
        self.endOfStream = endOfStream
    }

    func next(_ status: UnsafeMutablePointer<AVAudioConverterInputStatus>) -> AVAudioBuffer? {
        if let buffer {
            self.buffer = nil
            status.pointee = .haveData
            return buffer
        }
        status.pointee = endOfStream ? .endOfStream : .noDataNow
        return nil
    }
}
