import AVFoundation
import Foundation
import JarvisLiveKit

/// Speaks an ask's reply on this Mac (docs/plans/voice-push-to-talk.md).
///
/// **Never `AVSpeechSynthesizer.speak`**: its audio is believed to be rendered by a system daemon,
/// which the system-audio tap (it excludes only this process) would record as `them`. Instead the
/// synthesizer `write`s buffers, and they play on an `AVAudioEngine` in this process. The engine is
/// separate from `MicCapture`'s; how it sits beside the mic's voice processing is a hardware check
/// (TESTING.md, B30).
final class SpeechOutput: NSObject, AskSpeaker, AVSpeechSynthesizerDelegate, @unchecked Sendable {
    private let queue = DispatchQueue(label: "dev.apj.jarvis.ask-speech")
    private let synth = AVSpeechSynthesizer()
    private let engine = AVAudioEngine()
    private let player = AVAudioPlayerNode()
    private var connectedFormat: AVAudioFormat?
    private var current: Utterance?

    /// One reply being spoken. Touched only on `queue`.
    private final class Utterance {
        let onFirstAudio: @Sendable () -> Void
        let onDone: @Sendable () -> Void
        let onError: @Sendable () -> Void
        var heard = false
        var scheduled = 0
        var written = false
        var ended = false

        init(onFirstAudio: @escaping @Sendable () -> Void, onDone: @escaping @Sendable () -> Void, onError: @escaping @Sendable () -> Void) {
            self.onFirstAudio = onFirstAudio
            self.onDone = onDone
            self.onError = onError
        }
    }

    private static let playFormat = AVAudioFormat(standardFormatWithSampleRate: 22_050, channels: 1)!

    override init() {
        super.init()
        synth.delegate = self
        engine.attach(player)
    }

    func speak(
        _ text: String,
        onFirstAudio: @escaping @Sendable () -> Void,
        onDone: @escaping @Sendable () -> Void,
        onError: @escaping @Sendable () -> Void
    ) {
        queue.async { [self] in
            stopLocked()
            let u = Utterance(onFirstAudio: onFirstAudio, onDone: onDone, onError: onError)
            current = u
            let utterance = AVSpeechUtterance(string: text)
            utterance.voice = AVSpeechSynthesisVoice(language: "en-US")
            synth.write(utterance) { [weak self] buffer in
                guard let self, let pcm = buffer as? AVAudioPCMBuffer else { return }
                self.queue.async { self.received(pcm, for: u) }
            }
        }
    }

    func stop() {
        queue.async { [self] in stopLocked() }
    }

    // A last empty buffer usually marks the end of `write`; this covers the engines that skip it.
    func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didFinish utterance: AVSpeechUtterance) {
        queue.async { [self] in
            guard let u = current else { return }
            u.written = true
            finishIfDone(u)
        }
    }

    // MARK: - On `queue`

    private func received(_ pcm: AVAudioPCMBuffer, for u: Utterance) {
        guard u === current, !u.ended else { return }
        if pcm.frameLength == 0 {
            u.written = true
            finishIfDone(u)
            return
        }
        guard let buffer = convert(pcm) else { return fail(u) }
        do {
            try ensureRunning(format: buffer.format)
        } catch {
            return fail(u)
        }
        u.scheduled += 1
        player.scheduleBuffer(buffer, completionCallbackType: .dataPlayedBack) { [weak self] _ in
            guard let self else { return }
            self.queue.async {
                u.scheduled -= 1
                self.finishIfDone(u)
            }
        }
        if !player.isPlaying { player.play() }
        if !u.heard {
            u.heard = true
            u.onFirstAudio()  // the first buffer handed to the output: the nearest point to sound
        }
    }

    private func finishIfDone(_ u: Utterance) {
        guard u === current, !u.ended, u.written, u.scheduled == 0 else { return }
        u.ended = true
        current = nil
        u.heard ? u.onDone() : u.onError()
    }

    private func fail(_ u: Utterance) {
        guard !u.ended else { return }
        u.ended = true
        current = nil
        synth.stopSpeaking(at: .immediate)
        player.stop()
        u.onError()
    }

    private func stopLocked() {
        current?.ended = true
        current = nil
        synth.stopSpeaking(at: .immediate)
        player.stop()
    }

    private func ensureRunning(format: AVAudioFormat) throws {
        if connectedFormat != format {
            engine.disconnectNodeOutput(player)
            engine.connect(player, to: engine.mainMixerNode, format: format)
            connectedFormat = format
        }
        if !engine.isRunning {
            engine.prepare()
            try engine.start()
        }
    }

    /// The synthesizer may hand back integer PCM; the player node takes float.
    private func convert(_ pcm: AVAudioPCMBuffer) -> AVAudioPCMBuffer? {
        if pcm.format.commonFormat == .pcmFormatFloat32 { return pcm }
        let target = AVAudioFormat(standardFormatWithSampleRate: pcm.format.sampleRate, channels: pcm.format.channelCount)
            ?? Self.playFormat
        guard let converter = AVAudioConverter(from: pcm.format, to: target),
              let out = AVAudioPCMBuffer(pcmFormat: target, frameCapacity: pcm.frameLength) else { return nil }
        do {
            try converter.convert(to: out, from: pcm)
        } catch {
            return nil
        }
        return out
    }
}
