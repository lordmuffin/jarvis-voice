import AppKit

/// Watches a lone modifier key system-wide and feeds `DictationTrigger`.
///
/// Carbon hotkeys (see `HotKeys`) can't bind a modifier on its own, so this uses `NSEvent`
/// monitors: a global one for other apps (needs Accessibility) and a local one for our own windows.
/// Monitors only observe; every event still reaches the app it was meant for.
@MainActor
final class ModifierKeyMonitor {
    typealias Handler = @MainActor (DictationTrigger.Action) -> Void

    private static let escapeKeyCode: UInt16 = 53

    private var monitors: [Any] = []
    private var trigger: DictationTrigger?
    private var handler: Handler?
    private var holdTask: Task<Void, Never>?

    func start(key: TriggerKey, mode: TriggerMode, handler: @escaping Handler) {
        stop()
        guard key != .off else { return }
        trigger = DictationTrigger(key: key, mode: mode)
        self.handler = handler
        let mask: NSEvent.EventTypeMask = [.flagsChanged, .keyDown, .leftMouseDown, .rightMouseDown, .otherMouseDown]
        if let global = NSEvent.addGlobalMonitorForEvents(matching: mask, handler: { [weak self] event in
            MainActor.assumeIsolated { self?.handle(event) }
        }) {
            monitors.append(global)
        }
        if let local = NSEvent.addLocalMonitorForEvents(matching: mask, handler: { [weak self] event in
            MainActor.assumeIsolated { self?.handle(event) }
            return event
        }) {
            monitors.append(local)
        }
    }

    func stop() {
        for monitor in monitors { NSEvent.removeMonitor(monitor) }
        monitors = []
        holdTask?.cancel()
        holdTask = nil
        trigger = nil
        handler = nil
    }

    private func handle(_ event: NSEvent) {
        guard var trigger, let keyCode = trigger.key.keyCode else { return }
        if event.cgEvent?.getIntegerValueField(.eventSourceUserData) == TextInserter.eventTag { return }
        let action: DictationTrigger.Action?
        switch event.type {
        case .flagsChanged where event.keyCode == keyCode:
            if event.modifierFlags.rawValue & trigger.key.deviceMask != 0 {
                action = trigger.keyDown(at: event.timestamp)
                if trigger.mode == .hold { scheduleHoldCheck() }
            } else {
                holdTask?.cancel()
                action = trigger.keyUp(at: event.timestamp)
            }
        case .keyDown where event.keyCode == Self.escapeKeyCode:
            action = trigger.escape()
        default:
            action = trigger.otherInput()
        }
        self.trigger = trigger
        if let action { handler?(action) }
    }

    private func scheduleHoldCheck() {
        holdTask?.cancel()
        holdTask = Task { [weak self] in
            try? await Task.sleep(for: .seconds(DictationTrigger.holdThreshold))
            guard !Task.isCancelled else { return }
            self?.holdElapsed()
        }
    }

    private func holdElapsed() {
        guard var trigger else { return }
        let action = trigger.tick(at: ProcessInfo.processInfo.systemUptime)
        self.trigger = trigger
        if let action { handler?(action) }
    }
}
