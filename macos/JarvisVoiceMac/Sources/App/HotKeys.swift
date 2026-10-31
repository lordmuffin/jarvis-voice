import Carbon.HIToolbox
import Foundation

/// System-wide hotkeys through Carbon's `RegisterEventHotKey`, which needs no privacy permission.
@MainActor
final class HotKeys {
    enum Action: UInt32 { case toggleSession = 1, marker = 2 }

    private var refs: [EventHotKeyRef] = []
    private var installed = false
    nonisolated(unsafe) private static var handler: (@MainActor (Action) -> Void)?

    /// ⌃⌥J start/stop, ⌃⌥M marker.
    func register(_ handler: @escaping @MainActor (Action) -> Void) {
        Self.handler = handler
        if !installed {
            var spec = EventTypeSpec(
                eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed)
            )
            InstallEventHandler(GetApplicationEventTarget(), { _, event, _ -> OSStatus in
                var id = EventHotKeyID()
                GetEventParameter(
                    event, EventParamName(kEventParamDirectObject), EventParamType(typeEventHotKeyID),
                    nil, MemoryLayout<EventHotKeyID>.size, nil, &id
                )
                if let action = HotKeys.Action(rawValue: id.id) {
                    DispatchQueue.main.async { MainActor.assumeIsolated { HotKeys.handler?(action) } }
                }
                return noErr
            }, 1, &spec, nil, nil)
            installed = true
        }
        let modifiers = UInt32(controlKey | optionKey)
        add(keyCode: UInt32(kVK_ANSI_J), modifiers: modifiers, action: .toggleSession)
        add(keyCode: UInt32(kVK_ANSI_M), modifiers: modifiers, action: .marker)
    }

    private func add(keyCode: UInt32, modifiers: UInt32, action: Action) {
        var ref: EventHotKeyRef?
        let id = EventHotKeyID(signature: OSType(0x4A56_4D43), id: action.rawValue)  // 'JVMC'
        if RegisterEventHotKey(keyCode, modifiers, id, GetApplicationEventTarget(), 0, &ref) == noErr, let ref {
            refs.append(ref)
        }
    }
}
