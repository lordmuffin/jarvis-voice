import AVFoundation
import JarvisLiveKit
import XCTest

@testable import Jarvis_Voice

final class PauseTests: XCTestCase {
    func testLabelFlipsWithState() {
        XCTAssertEqual(PauseControl.label(isPaused: false), "Pause Session")
        XCTAssertEqual(PauseControl.label(isPaused: true), "Resume Session")
    }

    func testPanelButtonIsAGlyphInTheStripAndWordsWhenWide() {
        XCTAssertEqual(PauseControl.panelLabel(isPaused: false, strip: true), "\u{23F8}")
        XCTAssertEqual(PauseControl.panelLabel(isPaused: true, strip: true), "\u{25B6}")
        XCTAssertEqual(PauseControl.panelLabel(isPaused: false, strip: false), "\u{23F8} Pause Session")
        XCTAssertEqual(PauseControl.panelLabel(isPaused: true, strip: false), "\u{25B6} Resume Session")
        XCTAssertEqual(PauseControl.accessibilityLabel(isPaused: false), "Pause session")
        XCTAssertEqual(PauseControl.accessibilityLabel(isPaused: true), "Resume session")
    }

    func testPauseOnlyWhileRecording() {
        XCTAssertTrue(PauseControl.isAvailable(isRecording: true))
        XCTAssertFalse(PauseControl.isAvailable(isRecording: false))
    }

    func testMarkerAndAskNeedLiveAudio() {
        XCTAssertTrue(PauseControl.acceptsMarkerOrAsk(isRecording: true, isPaused: false))
        XCTAssertFalse(PauseControl.acceptsMarkerOrAsk(isRecording: true, isPaused: true))
        XCTAssertFalse(PauseControl.acceptsMarkerOrAsk(isRecording: false, isPaused: false))
    }

    func testStatusSaysPausedOnlyWhileRecording() {
        XCTAssertEqual(MenuText.status(link: .connected, isRecording: true, isPaused: true), "\u{23F8} Paused")
        XCTAssertEqual(MenuText.status(link: .connected, isRecording: true, isPaused: false), "\u{25CF} Live")
        XCTAssertEqual(MenuText.status(link: .idle, isRecording: false, isPaused: true), "\u{25CB} Idle")
        // The old two-argument form still means "not paused".
        XCTAssertEqual(MenuText.status(link: .idle, isRecording: true), "\u{25CF} Recording")
    }

    // MARK: Pipeline gate

    /// One second of 16 kHz mono silence, which the resampler turns into ten 100 ms frames.
    private func buffer() throws -> AVAudioPCMBuffer {
        let format = try XCTUnwrap(AVAudioFormat(standardFormatWithSampleRate: 16_000, channels: 1))
        let buf = try XCTUnwrap(AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 16_000))
        buf.frameLength = 16_000
        return buf
    }

    private func makePipeline() -> (ChannelPipeline, AsyncStream<FrameItem>.Continuation, AsyncStream<FrameItem>) {
        let (stream, continuation) = AsyncStream.makeStream(of: FrameItem.self)
        let pipeline = ChannelPipeline(
            channel: .mic, clock: SessionClock(), sink: continuation, transcriber: nil, onError: { _ in }
        )
        return (pipeline, continuation, stream)
    }

    /// `process` yields synchronously, so finishing the sink afterwards leaves exactly what was emitted.
    private func drain(_ stream: AsyncStream<FrameItem>) async -> [FrameItem] {
        var frames: [FrameItem] = []
        for await frame in stream { frames.append(frame) }
        return frames
    }

    func testPausedPipelineDropsAudioAndResumeRestartsIt() async throws {
        let (pipeline, sink, stream) = makePipeline()
        pipeline.setPaused(true)
        XCTAssertTrue(pipeline.isPaused)
        pipeline.process(try buffer())
        pipeline.setPaused(false)
        XCTAssertFalse(pipeline.isPaused)
        pipeline.process(try buffer())
        pipeline.finish()
        sink.finish()
        let frames = await drain(stream)
        XCTAssertFalse(frames.isEmpty, "audio after resume must reach the sink")
        XCTAssertLessThanOrEqual(frames.count, 10, "the paused second must not have been emitted")
    }

    func testNothingEmittedWhilePaused() async throws {
        let (pipeline, sink, stream) = makePipeline()
        pipeline.setPaused(true)
        pipeline.process(try buffer())
        pipeline.finish()
        sink.finish()
        let frames = await drain(stream)
        XCTAssertTrue(frames.isEmpty)
    }
}
