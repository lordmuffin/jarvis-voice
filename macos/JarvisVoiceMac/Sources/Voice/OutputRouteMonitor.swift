import CoreAudio
import Foundation
import JarvisLiveKit

/// Where a reply would play: the default output device (not the alert device), and whether it
/// keeps Jarvis in your ears (`OutputRoutes`). Calls `onChange` on the main queue whenever the
/// default device or its data source (headphones in or out of the jack) changes.
final class OutputRouteMonitor: @unchecked Sendable {
    private let onChange: @Sendable (Bool) -> Void
    private var watchedDevice = AudioObjectID(kAudioObjectUnknown)
    private var deviceListener: AudioObjectPropertyListenerBlock?
    private var sourceListener: AudioObjectPropertyListenerBlock?

    init(onChange: @escaping @Sendable (Bool) -> Void) {
        self.onChange = onChange
    }

    func start() {
        var addr = Self.address(kAudioHardwarePropertyDefaultOutputDevice)
        let block: AudioObjectPropertyListenerBlock = { [weak self] _, _ in self?.deviceChanged() }
        deviceListener = block
        AudioObjectAddPropertyListenerBlock(AudioObjectID(kAudioObjectSystemObject), &addr, .main, block)
        watch(Self.defaultOutput())
    }

    func stop() {
        if let deviceListener {
            var addr = Self.address(kAudioHardwarePropertyDefaultOutputDevice)
            AudioObjectRemovePropertyListenerBlock(AudioObjectID(kAudioObjectSystemObject), &addr, .main, deviceListener)
        }
        deviceListener = nil
        watch(AudioObjectID(kAudioObjectUnknown))
    }

    func isClosed() -> Bool {
        let device = Self.defaultOutput()
        guard device != kAudioObjectUnknown, let transport = Self.uint32(device, kAudioDevicePropertyTransportType)
        else { return false }
        let source = Self.uint32(device, kAudioDevicePropertyDataSource, scope: kAudioDevicePropertyScopeOutput)
        return OutputRoutes.isClosed(transport: transport, dataSource: source)
    }

    private func deviceChanged() {
        watch(Self.defaultOutput())
        onChange(isClosed())
    }

    /// Follows the data source of the current default device, so unplugging the jack counts.
    private func watch(_ device: AudioObjectID) {
        var addr = Self.address(kAudioDevicePropertyDataSource, scope: kAudioDevicePropertyScopeOutput)
        if let sourceListener, watchedDevice != kAudioObjectUnknown {
            AudioObjectRemovePropertyListenerBlock(watchedDevice, &addr, .main, sourceListener)
        }
        sourceListener = nil
        watchedDevice = device
        guard device != kAudioObjectUnknown else { return }
        let block: AudioObjectPropertyListenerBlock = { [weak self] _, _ in
            guard let self else { return }
            self.onChange(self.isClosed())
        }
        if AudioObjectAddPropertyListenerBlock(device, &addr, .main, block) == noErr { sourceListener = block }
    }

    private static func address(
        _ selector: AudioObjectPropertySelector, scope: AudioObjectPropertyScope = kAudioObjectPropertyScopeGlobal
    ) -> AudioObjectPropertyAddress {
        AudioObjectPropertyAddress(mSelector: selector, mScope: scope, mElement: kAudioObjectPropertyElementMain)
    }

    private static func defaultOutput() -> AudioObjectID {
        var addr = address(kAudioHardwarePropertyDefaultOutputDevice)
        var device = AudioObjectID(kAudioObjectUnknown)
        var size = UInt32(MemoryLayout<AudioObjectID>.size)
        let status = AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &size, &device)
        return status == noErr ? device : AudioObjectID(kAudioObjectUnknown)
    }

    private static func uint32(
        _ object: AudioObjectID, _ selector: AudioObjectPropertySelector,
        scope: AudioObjectPropertyScope = kAudioObjectPropertyScopeGlobal
    ) -> UInt32? {
        var addr = address(selector, scope: scope)
        guard AudioObjectHasProperty(object, &addr) else { return nil }
        var value: UInt32 = 0
        var size = UInt32(MemoryLayout<UInt32>.size)
        return AudioObjectGetPropertyData(object, &addr, 0, nil, &size, &value) == noErr ? value : nil
    }
}
