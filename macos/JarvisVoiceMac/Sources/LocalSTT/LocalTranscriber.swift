import AVFoundation
import Foundation
import JarvisLiveKit
import os
import Speech

/// One on-device `SpeechAnalyzer` + `SpeechTranscriber` for a single channel (en-US, volatile
/// results on). The only local STT in v0: it uses the system speech asset, nothing else is
/// downloaded.
///
/// Threading: `feed(_:)` is called from exactly one capture thread (the channel's tap or IO
/// proc). Everything else happens on the structured tasks started by `start()`.
final class LocalTranscriber: @unchecked Sendable {
    struct Emission: Sendable {
        var draft: DraftSegment
        /// Volatile updates are throttled before going on the wire; finals always are sent.
        var sendToServer: Bool
    }

    static let locale = Locale(identifier: "en-US")
    private static let volatileSendInterval: TimeInterval = 0.4

    let channel: Channel
    /// Session time of the first buffer fed; analyzer time starts at zero there.
    private let offsetMs = OSAllocatedUnfairLock<Int?>(initialState: nil)
    private let onEmission: @Sendable (Emission) -> Void

    private var transcriber: SpeechTranscriber?
    private var analyzer: SpeechAnalyzer?
    private var inputContinuation: AsyncStream<AnalyzerInput>.Continuation?
    private var resultsTask: Task<Void, Never>?
    private var targetFormat: AVAudioFormat?
    private var converter: AVAudioConverter?
    private var converterSource: AVAudioFormat?
    private var lastVolatileSend = Date.distantPast

    init(channel: Channel, onEmission: @escaping @Sendable (Emission) -> Void) {
        self.channel = channel
        self.onEmission = onEmission
    }

    // MARK: - Assets

    /// Installs the en-US speech asset if needed, reporting progress in 0...1.
    static func ensureAssets(progress: @escaping @Sendable (Double) -> Void) async throws {
        guard SpeechTranscriber.isAvailable else { throw LocalTranscriberError.unavailable }
        guard let locale = await SpeechTranscriber.supportedLocale(equivalentTo: Self.locale) else {
            throw LocalTranscriberError.localeUnsupported
        }
        let transcriber = makeTranscriber(locale: locale)
        guard let request = try await AssetInventory.assetInstallationRequest(supporting: [transcriber]) else {
            return  // Already installed.
        }
        let poll = Task {
            while !Task.isCancelled {
                progress(request.progress.fractionCompleted)
                try? await Task.sleep(for: .milliseconds(250))
            }
        }
        defer { poll.cancel() }
        try await request.downloadAndInstall()
        progress(1)
    }

    private static func makeTranscriber(locale: Locale) -> SpeechTranscriber {
        SpeechTranscriber(
            locale: locale,
            transcriptionOptions: [],
            reportingOptions: [.volatileResults],
            attributeOptions: [.audioTimeRange]
        )
    }

    // MARK: - Lifecycle

    func start() async throws {
        let locale = await SpeechTranscriber.supportedLocale(equivalentTo: Self.locale) ?? Self.locale
        let transcriber = Self.makeTranscriber(locale: locale)
        let analyzer = SpeechAnalyzer(modules: [transcriber])
        guard let format = await SpeechAnalyzer.bestAvailableAudioFormat(compatibleWith: [transcriber]) else {
            throw LocalTranscriberError.noCompatibleFormat
        }
        targetFormat = format
        self.transcriber = transcriber
        self.analyzer = analyzer

        let (stream, continuation) = AsyncStream.makeStream(of: AnalyzerInput.self)
        inputContinuation = continuation
        try await analyzer.start(inputSequence: stream)

        let channel = channel
        let offsetMs = offsetMs
        resultsTask = Task { [weak self] in
            do {
                for try await result in transcriber.results {
                    guard let self else { return }
                    let text = String(result.text.characters).trimmingCharacters(in: .whitespacesAndNewlines)
                    guard !text.isEmpty else { continue }
                    let base = offsetMs.withLock { $0 ?? 0 }
                    let start = base + Int((result.range.start.seconds * 1000).rounded())
                    let end = base + Int((result.range.end.seconds * 1000).rounded())
                    let draft = DraftSegment(
                        channel: channel, startMs: max(0, start), endMs: max(0, end, start),
                        text: text, final: result.isFinal
                    )
                    self.emit(draft)
                }
            } catch {
                // The analyzer was cancelled or failed; the capture pipeline keeps recording audio.
            }
        }
    }

    private func emit(_ draft: DraftSegment) {
        var send = true
        if !draft.final {
            let now = Date()
            send = now.timeIntervalSince(lastVolatileSend) >= Self.volatileSendInterval
            if send { lastVolatileSend = now }
        }
        onEmission(Emission(draft: draft, sendToServer: send))
    }

    /// Converts `buffer` to the analyzer's format and queues it. Call from the capture thread only.
    func feed(_ buffer: AVAudioPCMBuffer, sessionMs: Int) {
        offsetMs.withLock { if $0 == nil { $0 = sessionMs } }
        guard let continuation = inputContinuation, let target = targetFormat else { return }
        guard let converted = convert(buffer, to: target) else { return }
        continuation.yield(AnalyzerInput(buffer: converted))
    }

    /// Ends input and waits for the final results to be delivered.
    func finish() async {
        inputContinuation?.finish()
        inputContinuation = nil
        do {
            try await analyzer?.finalizeAndFinishThroughEndOfInput()
        } catch {
            await analyzer?.cancelAndFinishNow()
        }
        await resultsTask?.value
        resultsTask = nil
        analyzer = nil
        transcriber = nil
    }

    // MARK: - Conversion

    private func convert(_ buffer: AVAudioPCMBuffer, to target: AVAudioFormat) -> AVAudioPCMBuffer? {
        if buffer.format == target { return copy(buffer) }
        if converter == nil || converterSource != buffer.format {
            converter = AVAudioConverter(from: buffer.format, to: target)
            converterSource = buffer.format
        }
        guard let converter else { return nil }
        let ratio = target.sampleRate / buffer.format.sampleRate
        let capacity = AVAudioFrameCount((Double(buffer.frameLength) * ratio).rounded(.up)) + 64
        guard let out = AVAudioPCMBuffer(pcmFormat: target, frameCapacity: capacity) else { return nil }
        let feed = OneShotInput(buffer)
        var error: NSError?
        let status = converter.convert(to: out, error: &error) { _, outStatus in feed.next(outStatus) }
        return status == .error || out.frameLength == 0 ? nil : out
    }

    /// The tap's buffer is only valid for the duration of the callback, so the analyzer gets a copy.
    private func copy(_ buffer: AVAudioPCMBuffer) -> AVAudioPCMBuffer? {
        guard let out = AVAudioPCMBuffer(pcmFormat: buffer.format, frameCapacity: buffer.frameLength) else { return nil }
        out.frameLength = buffer.frameLength
        let source = UnsafeMutableAudioBufferListPointer(UnsafeMutablePointer(mutating: buffer.audioBufferList))
        let destination = UnsafeMutableAudioBufferListPointer(out.mutableAudioBufferList)
        for (src, dst) in zip(source, destination) {
            guard let from = src.mData, let to = dst.mData else { continue }
            memcpy(to, from, Int(min(src.mDataByteSize, dst.mDataByteSize)))
        }
        return out
    }
}

private final class OneShotInput: @unchecked Sendable {
    private var buffer: AVAudioPCMBuffer?
    init(_ buffer: AVAudioPCMBuffer) { self.buffer = buffer }
    func next(_ status: UnsafeMutablePointer<AVAudioConverterInputStatus>) -> AVAudioBuffer? {
        if let buffer {
            self.buffer = nil
            status.pointee = .haveData
            return buffer
        }
        status.pointee = .noDataNow
        return nil
    }
}

enum LocalTranscriberError: Error, LocalizedError {
    case unavailable
    case localeUnsupported
    case noCompatibleFormat

    var errorDescription: String? {
        switch self {
        case .unavailable: "On-device speech transcription is not available on this Mac."
        case .localeUnsupported: "English (US) on-device transcription is not supported here."
        case .noCompatibleFormat: "No audio format is compatible with the speech analyzer."
        }
    }
}
