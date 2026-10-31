import AVFoundation
import CoreAudio
import Foundation

/// Captures all system output, excluding this process, with a Core Audio process tap wrapped in a
/// private aggregate device (the structure used by the open-source `insidegui/AudioCap` sample).
///
/// Permission: macOS gates this behind the "System Audio Recording" (audio capture) privacy
/// setting. There is no public pre-flight, and a denied tap delivers silence rather than failing,
/// so a watchdog reports `.noAudio` when nothing but digital silence arrives for a while.
final class SystemCapture: @unchecked Sendable {
    enum State: Equatable, Sendable {
        case idle
        case running
        /// Setup failed outright; the message says why.
        case failed(String)
        /// The tap runs but has produced only digital silence: usually permission denied.
        case noAudio
    }

    typealias Handler = @Sendable (AVAudioPCMBuffer) -> Void

    private var tapID = AudioObjectID(kAudioObjectUnknown)
    private var aggregateID = AudioObjectID(kAudioObjectUnknown)
    private var procID: AudioDeviceIOProcID?
    private let queue = DispatchQueue(label: "dev.apj.jarvis.system-capture", qos: .userInteractive)

    // Silence watchdog state, touched only from `queue`.
    private var sawAudible = false
    private var silentSince: Date?
    private var reportedNoAudio = false
    private var onState: (@Sendable (State) -> Void)?

    func start(handler: @escaping Handler, onState: @escaping @Sendable (State) -> Void) {
        self.onState = onState
        do {
            try startTap(handler: handler)
            onState(.running)
        } catch {
            teardown()
            onState(.failed(error.localizedDescription))
        }
    }

    func stop() {
        teardown()
        onState = nil
    }

    // MARK: - Setup

    private func startTap(handler: @escaping Handler) throws {
        guard let outputUID = AudioDevices.defaultOutputUID() else { throw SystemCaptureError.noOutputDevice }
        let excluded = AudioDevices.ownProcessObject().map { [$0] } ?? []

        let description = CATapDescription(stereoGlobalTapButExcludeProcesses: excluded)
        description.uuid = UUID()
        description.name = "Jarvis Voice system tap"
        description.muteBehavior = .unmuted
        description.isPrivate = true

        var status = AudioHardwareCreateProcessTap(description, &tapID)
        guard status == noErr else { throw SystemCaptureError.osStatus("create process tap", status) }

        let aggregate: [String: Any] = [
            kAudioAggregateDeviceNameKey: "Jarvis Voice tap",
            kAudioAggregateDeviceUIDKey: UUID().uuidString,
            kAudioAggregateDeviceMainSubDeviceKey: outputUID,
            kAudioAggregateDeviceIsPrivateKey: true,
            kAudioAggregateDeviceIsStackedKey: false,
            kAudioAggregateDeviceTapAutoStartKey: true,
            kAudioAggregateDeviceSubDeviceListKey: [[kAudioSubDeviceUIDKey: outputUID]],
            kAudioAggregateDeviceTapListKey: [[
                kAudioSubTapDriftCompensationKey: true,
                kAudioSubTapUIDKey: description.uuid.uuidString,
            ]],
        ]
        status = AudioHardwareCreateAggregateDevice(aggregate as CFDictionary, &aggregateID)
        guard status == noErr else { throw SystemCaptureError.osStatus("create aggregate device", status) }

        var asbd = AudioStreamBasicDescription()
        var size = UInt32(MemoryLayout<AudioStreamBasicDescription>.size)
        var formatAddress = AudioObjectPropertyAddress(
            mSelector: kAudioTapPropertyFormat, mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        status = AudioObjectGetPropertyData(tapID, &formatAddress, 0, nil, &size, &asbd)
        guard status == noErr, let format = AVAudioFormat(streamDescription: &asbd) else {
            throw SystemCaptureError.osStatus("read tap format", status)
        }

        sawAudible = false
        silentSince = nil
        reportedNoAudio = false

        status = AudioDeviceCreateIOProcIDWithBlock(&procID, aggregateID, queue) {
            [weak self] _, inputData, _, _, _ in
            guard let self,
                  let buffer = AVAudioPCMBuffer(pcmFormat: format, bufferListNoCopy: inputData, deallocator: nil)
            else { return }
            self.watchSilence(buffer)
            handler(buffer)
        }
        guard status == noErr else { throw SystemCaptureError.osStatus("create IO proc", status) }

        status = AudioDeviceStart(aggregateID, procID)
        guard status == noErr else { throw SystemCaptureError.osStatus("start device", status) }
    }

    private func teardown() {
        if aggregateID != kAudioObjectUnknown {
            if let procID {
                AudioDeviceStop(aggregateID, procID)
                AudioDeviceDestroyIOProcID(aggregateID, procID)
            }
            AudioHardwareDestroyAggregateDevice(aggregateID)
        }
        if tapID != kAudioObjectUnknown { AudioHardwareDestroyProcessTap(tapID) }
        procID = nil
        aggregateID = AudioObjectID(kAudioObjectUnknown)
        tapID = AudioObjectID(kAudioObjectUnknown)
    }

    // MARK: - Silence watchdog

    private static let silenceGrace: TimeInterval = 8

    private func watchSilence(_ buffer: AVAudioPCMBuffer) {
        if Self.isAudible(buffer) {
            sawAudible = true
            silentSince = nil
            if reportedNoAudio {
                reportedNoAudio = false
                onState?(.running)
            }
            return
        }
        let since = silentSince ?? Date()
        silentSince = since
        if !sawAudible, !reportedNoAudio, Date().timeIntervalSince(since) > Self.silenceGrace {
            reportedNoAudio = true
            onState?(.noAudio)
        }
    }

    private static func isAudible(_ buffer: AVAudioPCMBuffer) -> Bool {
        guard let data = buffer.floatChannelData else { return true }  // Unknown layout: assume audible.
        let frames = Int(buffer.frameLength)
        for channel in 0..<Int(buffer.format.channelCount) {
            let samples = data[channel]
            for i in stride(from: 0, to: frames, by: 8) where samples[i] != 0 { return true }
        }
        return false
    }
}

enum SystemCaptureError: Error, LocalizedError {
    case noOutputDevice
    case osStatus(String, OSStatus)

    var errorDescription: String? {
        switch self {
        case .noOutputDevice: "No default output device to tap."
        case .osStatus(let what, let status): "Could not \(what) (OSStatus \(status))."
        }
    }
}
