import AVFoundation
import Foundation
import JarvisLiveKit

struct SessionClock: Sendable {
    let start: ContinuousClock.Instant

    init() { start = .now }

    func elapsedMs() -> Int {
        let d = start.duration(to: .now)
        return Int(d.components.seconds) * 1000 + Int(d.components.attoseconds / 1_000_000_000_000_000)
    }
}

struct FrameItem: Sendable {
    var channel: Channel
    var payload: Data
    var tMs: UInt32
}

/// Per-channel path from raw capture buffers to wire frames and the on-device transcriber.
///
/// Exactly one capture thread calls `process`, and `finish` is called only after that capture has
/// been stopped. The one thing set from another thread is the pause flag, which is lock-guarded.
///
/// Pause: the capture engines keep running and `process` drops buffers while paused, so nothing
/// reaches the on-device transcriber, the transport or the outbox. The session clock keeps running,
/// so after a resume the next frame is stamped with wall-clock time (a gap in the transcript, no
/// shifting of later audio): resume re-anchors `offsetMs` and restarts the frame count.
final class ChannelPipeline: @unchecked Sendable {
    let channel: Channel
    private let resampler = Resampler()
    private let sink: AsyncStream<FrameItem>.Continuation
    private let transcriber: LocalTranscriber?
    private let clock: SessionClock
    private let onError: @Sendable (String) -> Void
    private var offsetMs: UInt32?
    private var frameCount: UInt32 = 0
    private var reportedError = false
    private let pauseLock = NSLock()
    private var paused = false

    init(
        channel: Channel,
        clock: SessionClock,
        sink: AsyncStream<FrameItem>.Continuation,
        transcriber: LocalTranscriber?,
        onError: @escaping @Sendable (String) -> Void
    ) {
        self.channel = channel
        self.clock = clock
        self.sink = sink
        self.transcriber = transcriber
        self.onError = onError
    }

    /// Drops (true) or passes (false) audio. Safe from any thread.
    func setPaused(_ value: Bool) {
        pauseLock.lock()
        defer { pauseLock.unlock() }
        paused = value
    }

    var isPaused: Bool {
        pauseLock.lock()
        defer { pauseLock.unlock() }
        return paused
    }

    func process(_ buffer: AVAudioPCMBuffer) {
        if isPaused {
            // Re-anchor on the first buffer after resume; a few ms the resampler still holds are dropped.
            offsetMs = nil
            frameCount = 0
            return
        }
        if offsetMs == nil { offsetMs = UInt32(clamping: clock.elapsedMs()) }
        transcriber?.feed(buffer, sessionMs: Int(offsetMs ?? 0))
        do {
            for payload in try resampler.convert(buffer) { emit(payload) }
        } catch {
            fail(error)
        }
    }

    func finish() {
        do {
            for payload in try resampler.finish() { emit(payload) }
        } catch {
            fail(error)
        }
    }

    private func emit(_ payload: Data) {
        let tMs = (offsetMs ?? 0) &+ frameCount &* UInt32(Resampler.frameMs)
        frameCount &+= 1
        sink.yield(FrameItem(channel: channel, payload: payload, tMs: tMs))
    }

    private func fail(_ error: Error) {
        guard !reportedError else { return }
        reportedError = true
        onError("Audio conversion failed on \(channel.rawValue): \(error.localizedDescription)")
    }
}
