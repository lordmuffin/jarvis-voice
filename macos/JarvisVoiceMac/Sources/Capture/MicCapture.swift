import AVFoundation
import CoreAudio
import Foundation

/// Captures the selected input device with `AVAudioEngine`.
///
/// Voice processing is enabled on the input node for echo cancellation (the primary fix for the
/// laptop-speaker case) and other-audio ducking is set to the minimum level so a call's far end
/// is not attenuated. If a device refuses voice processing the capture continues without it and
/// `voiceProcessingActive` reports `false`.
final class MicCapture: @unchecked Sendable {
    typealias Handler = @Sendable (AVAudioPCMBuffer) -> Void

    private(set) var voiceProcessingActive = false
    private var engine: AVAudioEngine?
    private var handler: Handler?
    private var deviceUID: String?
    private var configObserver: NSObjectProtocol?
    private var monoFormat: AVAudioFormat?

    /// Starts capture. Throws if the device cannot be opened.
    func start(deviceUID: String?, handler: @escaping Handler) throws {
        self.handler = handler
        self.deviceUID = deviceUID
        try startEngine()
    }

    func stop() {
        if let configObserver { NotificationCenter.default.removeObserver(configObserver) }
        configObserver = nil
        engine?.inputNode.removeTap(onBus: 0)
        engine?.stop()
        engine = nil
        handler = nil
    }

    private func startEngine() throws {
        let engine = AVAudioEngine()
        let input = engine.inputNode

        // Select the device before enabling voice processing: enabling it can swap the unit's device.
        if let deviceUID, var deviceID = AudioDevices.deviceID(forUID: deviceUID), let unit = input.audioUnit {
            AudioUnitSetProperty(
                unit, kAudioOutputUnitProperty_CurrentDevice, kAudioUnitScope_Global, 0,
                &deviceID, UInt32(MemoryLayout<AudioDeviceID>.size)
            )
        }

        do {
            try input.setVoiceProcessingEnabled(true)
            input.voiceProcessingOtherAudioDuckingConfiguration = AVAudioVoiceProcessingOtherAudioDuckingConfiguration(
                enableAdvancedDucking: false, duckingLevel: .min
            )
            voiceProcessingActive = true
        } catch {
            voiceProcessingActive = false
        }

        let format = input.outputFormat(forBus: 0)
        guard format.sampleRate > 0, format.channelCount > 0 else {
            throw MicCaptureError.noInput
        }
        // Voice processing can expose many channels (one per mic element). Only channel 0 carries the
        // processed voice; take it explicitly rather than relying on a multi-channel downmix.
        let needsMono = format.channelCount > 2
        monoFormat = needsMono
            ? AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: format.sampleRate, channels: 1, interleaved: false)
            : nil

        input.installTap(onBus: 0, bufferSize: 4096, format: format) { [weak self] buffer, _ in
            guard let self, let handler = self.handler else { return }
            if let mono = self.monoFormat {
                handler(Self.firstChannel(of: buffer, as: mono) ?? buffer)
            } else {
                handler(buffer)
            }
        }
        engine.prepare()
        try engine.start()
        self.engine = engine

        configObserver = NotificationCenter.default.addObserver(
            forName: .AVAudioEngineConfigurationChange, object: engine, queue: nil
        ) { [weak self] _ in
            // Device switch or format change: rebuild on the same handler.
            guard let self, self.handler != nil else { return }
            self.engine?.inputNode.removeTap(onBus: 0)
            self.engine?.stop()
            if let observer = self.configObserver { NotificationCenter.default.removeObserver(observer) }
            self.configObserver = nil
            try? self.startEngine()
        }
    }

    private static func firstChannel(of buffer: AVAudioPCMBuffer, as format: AVAudioFormat) -> AVAudioPCMBuffer? {
        guard let source = buffer.floatChannelData,
              let out = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: buffer.frameLength),
              let destination = out.floatChannelData else { return nil }
        out.frameLength = buffer.frameLength
        destination[0].update(from: source[0], count: Int(buffer.frameLength))
        return out
    }
}

enum MicCaptureError: Error, LocalizedError {
    case noInput
    var errorDescription: String? { "The selected input device reports no audio format." }
}
