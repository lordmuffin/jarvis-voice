import CoreAudio
import Foundation

/// What the ask control shows.
public enum AskUIState: Equatable, Sendable {
    /// The server has not offered asks: local-only, voice off, an old server, or no connection.
    case hidden
    case ready
    case listening
    case waiting
    case speaking(String)
    case answered(String, spoken: Bool, why: AskWhy?)
}

/// Why an answer was shown and not spoken.
public enum AskWhy: Equatable, Sendable { case route, stale, nothingHeard, speechFailed, interrupted }

/// Speech on this Mac. Callbacks may come on any thread, or before `speak` returns.
public protocol AskSpeaker: AnyObject, Sendable {
    func speak(
        _ text: String,
        onFirstAudio: @escaping @Sendable () -> Void,
        onDone: @escaping @Sendable () -> Void,
        onError: @escaping @Sendable () -> Void
    )
    func stop()
}

/// Which outputs keep Jarvis in your ears (docs/plans/voice-push-to-talk.md, Headphones only).
/// Anything not known to be closed is open: built-in speakers, displays, AirPlay, aggregates.
public enum OutputRoutes {
    /// Built-in output's data source when headphones are plugged in.
    static let headphonesSource: UInt32 = 0x6864_706E  // 'hdpn'

    public static func isClosed(transport: UInt32, dataSource: UInt32?) -> Bool {
        switch transport {
        case kAudioDeviceTransportTypeBluetooth, kAudioDeviceTransportTypeBluetoothLE, kAudioDeviceTransportTypeUSB:
            return true
        case kAudioDeviceTransportTypeBuiltIn:
            return dataSource == headphonesSource
        default:
            return false
        }
    }
}

/// Hold to ask, on the Mac: markers out on the audio frames' clock, the reply spoken only into
/// closed headphones, and what happened reported back. No audio APIs here, so all of it is tested;
/// the app supplies the speaker, the route check and the send.
public final class AskController: @unchecked Sendable {
    /// A reply later than this after release is shown, not spoken: its moment has passed.
    public static let staleMs = 30_000

    private let send: @Sendable (ClientMessage) -> Void
    private let speaker: AskSpeaker
    private let routeClosed: @Sendable () -> Bool
    private let clock: @Sendable () -> Int
    private let streamMs: @Sendable () -> Int
    private let newId: @Sendable () -> String
    private let stale: Int
    private let onChange: (@Sendable (AskUIState) -> Void)?

    private let lock = NSRecursiveLock()
    private var available = false
    private var current: String?
    private var releasedAt = 0
    private var playing: (id: String, text: String)?
    private var state: AskUIState = .hidden

    public init(
        send: @escaping @Sendable (ClientMessage) -> Void,
        speaker: AskSpeaker,
        routeClosed: @escaping @Sendable () -> Bool,
        clock: @escaping @Sendable () -> Int,
        streamMs: @escaping @Sendable () -> Int,
        newId: @escaping @Sendable () -> String = { String(UUID().uuidString.prefix(8)).lowercased() },
        staleMs: Int = AskController.staleMs,
        onChange: (@Sendable (AskUIState) -> Void)? = nil
    ) {
        self.send = send
        self.speaker = speaker
        self.routeClosed = routeClosed
        self.clock = clock
        self.streamMs = streamMs
        self.newId = newId
        self.stale = staleMs
        self.onChange = onChange
    }

    public var ui: AskUIState {
        lock.lock()
        defer { lock.unlock() }
        return state
    }

    public func onAvailable(_ value: Bool) {
        lock.lock()
        defer { lock.unlock() }
        available = value
        if !value {
            stopPlaying(reason: nil)
            current = nil
            set(.hidden)
        } else if state == .hidden {
            set(.ready)
        }
    }

    public func down(tMs: Int) {
        lock.lock()
        defer { lock.unlock() }
        guard available else { return }
        stopPlaying(reason: .superseded)
        let id = newId()
        current = id
        send(.askBegin(AskBegin(id: id, tMs: tMs)))
        set(.listening)
    }

    public func up(tMs: Int) {
        lock.lock()
        defer { lock.unlock() }
        guard let id = current, state == .listening else { return }
        send(.askEnd(AskEnd(id: id, tMs: tMs)))
        releasedAt = clock()
        set(.waiting)
    }

    public func onReply(_ reply: AskReply) {
        lock.lock()
        defer { lock.unlock() }
        guard reply.id == current, state == .waiting else { return }
        let text = reply.text
        if text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            set(.answered("", spoken: false, why: .nothingHeard))
        } else if clock() - releasedAt > stale {
            skip(reply.id, text, .stale, why: .stale)
        } else if !routeClosed() {
            skip(reply.id, text, .route, why: .route)
        } else {
            let id = reply.id
            playing = (id, text)
            speaker.speak(
                text,
                onFirstAudio: { [weak self] in self?.firstAudio(id, text) },
                onDone: { [weak self] in self?.finished(id, text) },
                onError: { [weak self] in self?.failed(id, text) }
            )
        }
    }

    /// The output route changed. Leaving closed headphones stops speech at once.
    public func onRouteChanged(closed: Bool) {
        lock.lock()
        defer { lock.unlock() }
        guard !closed, let text = playing?.text else { return }
        stopPlaying(reason: .interrupted)
        set(.answered(text, spoken: false, why: .interrupted))
    }

    private func firstAudio(_ id: String, _ text: String) {
        lock.lock()
        defer { lock.unlock() }
        guard playing?.id == id else { return }
        send(.askPlayed(AskPlayed(id: id, state: .started, tMs: streamMs(), latencyMs: clock() - releasedAt)))
        set(.speaking(text))
    }

    private func finished(_ id: String, _ text: String) {
        lock.lock()
        defer { lock.unlock() }
        guard playing?.id == id else { return }
        playing = nil
        send(.askPlayed(AskPlayed(id: id, state: .done, tMs: streamMs())))
        set(.answered(text, spoken: true, why: nil))
    }

    private func failed(_ id: String, _ text: String) {
        lock.lock()
        defer { lock.unlock() }
        guard playing?.id == id else { return }
        playing = nil
        send(.askPlayed(AskPlayed(id: id, state: .skipped, tMs: streamMs(), reason: .ttsFailed)))
        set(.answered(text, spoken: false, why: .speechFailed))
    }

    private func skip(_ id: String, _ text: String, _ reason: AskSkipReason, why: AskWhy) {
        send(.askPlayed(AskPlayed(id: id, state: .skipped, tMs: streamMs(), reason: reason)))
        set(.answered(text, spoken: false, why: why))
    }

    /// Stops speech in progress. `reason` nil: the connection is gone, so nothing is reported.
    private func stopPlaying(reason: AskSkipReason?) {
        guard let id = playing?.id else { return }
        playing = nil
        speaker.stop()
        if let reason { send(.askPlayed(AskPlayed(id: id, state: .skipped, tMs: streamMs(), reason: reason))) }
    }

    private func set(_ new: AskUIState) {
        state = new
        onChange?(new)
    }
}
