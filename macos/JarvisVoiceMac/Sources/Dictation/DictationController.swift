import AVFoundation
import Foundation
import JarvisLiveKit
import Observation

/// Quick dictation: a lone modifier key records the mic, transcribes it on this Mac, copies the
/// text and pastes it at the cursor.
///
/// Entirely separate from recording sessions: its own `MicCapture` (no voice processing) and its
/// own `LocalTranscriber`. Nothing goes to the server, the outbox or the local transcript files.
@MainActor
@Observable
final class DictationController {
    enum State: Equatable { case idle, listening, finishing }

    /// Stops a forgotten tap-mode dictation so the mic doesn't stay open.
    private static let maxDuration: Duration = .seconds(300)
    /// Longest wait after the key is released for the analyzer to start, and then to finish.
    private static let finishTimeout: Duration = .seconds(15)

    private(set) var state: State = .idle
    /// Finals so far plus the current volatile guess, for the pill.
    private(set) var liveText = ""
    private(set) var assetProgress: Double?
    /// A short outcome shown in the pill after it stops ("Copied", an error).
    private(set) var message: String?
    private(set) var accessibilityTrusted = false

    @ObservationIgnored private let settings: SettingsModel
    @ObservationIgnored private let monitor = ModifierKeyMonitor()
    @ObservationIgnored private lazy var hud = DictationHUD(dictation: self)
    @ObservationIgnored private var capture: Capture?
    @ObservationIgnored private var messageTask: Task<Void, Never>?
    @ObservationIgnored private var trustObserver: NSObjectProtocol?
    @ObservationIgnored private var askingForMicrophone = false
    @ObservationIgnored private var assetsTask: Task<Void, Never>?
    /// Bumped whenever a dictation ends early, so its in-flight tasks stand down.
    @ObservationIgnored private var generation = 0

    init(settings: SettingsModel) {
        self.settings = settings
        accessibilityTrusted = TextInserter.isTrusted
    }

    var isActive: Bool { state != .idle }

    // MARK: Trigger key

    /// Installs the trigger-key monitor for the current settings. Call again when they change.
    func startMonitoring() {
        cancel()
        monitor.start(key: settings.dictationKey, mode: settings.dictationMode) { [weak self] action in
            self?.handle(action)
        }
        guard settings.dictationKey != .off else { return }
        prepareAssets()
        if trustObserver == nil {
            // Posted when any app's Accessibility grant changes. A monitor installed before the
            // grant may never see key events, so it is reinstalled.
            trustObserver = DistributedNotificationCenter.default().addObserver(
                forName: Notification.Name("com.apple.accessibility.api"), object: nil, queue: .main
            ) { [weak self] _ in
                Task { @MainActor [weak self] in
                    try? await Task.sleep(for: .milliseconds(500))
                    self?.refreshTrust()
                }
            }
        }
    }

    /// Shows the system Accessibility prompt once, if dictation is on and the app isn't trusted.
    func requestAccessibilityIfNeeded() {
        guard settings.dictationKey != .off, !TextInserter.isTrusted else { return }
        TextInserter.requestTrust()
    }

    func refreshTrust() {
        let trusted = TextInserter.isTrusted
        guard trusted != accessibilityTrusted else { return }
        accessibilityTrusted = trusted
        if state == .idle { startMonitoring() }
    }

    private func handle(_ action: DictationTrigger.Action) {
        switch action {
        case .begin: begin()
        case .commit: commit()
        case .cancel: cancel()
        case .toggle: state == .idle ? begin() : commit()
        }
    }

    // MARK: Lifecycle

    func begin() {
        guard state == .idle else { return }
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized:
            break
        case .notDetermined:
            // This press only asks: the prompt outlasts it, and the next press records.
            requestMicrophone()
            return
        default:
            flash(AppError.micDenied.localizedDescription)
            return
        }

        generation &+= 1
        let gen = generation
        let capture = Capture()
        self.capture = capture
        state = .listening
        liveText = ""
        clearMessage()
        hud.show()

        // Open the mic now so the first word is kept while the analyzer starts.
        do {
            try startMic(capture)
        } catch {
            fail(error.localizedDescription)
            return
        }

        let collector = capture.collector
        capture.startTask = Task { [weak self] in
            do {
                try await LocalTranscriber.ensureAssets { [weak self] fraction in
                    Task { @MainActor [weak self] in
                        guard let self, gen == self.generation, !capture.assetsReady else { return }
                        self.assetProgress = fraction
                    }
                }
                capture.assetsReady = true
                self?.assetProgress = nil
                let transcriber = LocalTranscriber(channel: .mic) { [weak self] emission in
                    let live = collector.add(emission.draft)
                    Task { @MainActor [weak self] in
                        guard let self, gen == self.generation else { return }
                        self.liveText = live
                    }
                }
                try await transcriber.start()
                guard let self, gen == self.generation else {
                    await transcriber.finish()
                    return
                }
                capture.preRoll.attach(transcriber)
            } catch {
                guard let self, gen == self.generation else { return }
                self.fail(error.localizedDescription)
            }
        }

        capture.limitTask = Task { [weak self] in
            try? await Task.sleep(for: Self.maxDuration)
            guard !Task.isCancelled, let self, gen == self.generation, self.state == .listening else { return }
            self.commit()
        }
    }

    /// Stops listening, transcribes what is left, then copies and pastes the text.
    func commit() {
        guard state == .listening, let capture else { return }
        state = .finishing
        capture.limitTask?.cancel()
        // The mic closes as soon as the user is done; audio not yet transcribed waits in the pre-roll.
        capture.mic.stop()
        let gen = generation
        let startTask = capture.startTask
        Task { [weak self] in
            // Bounded, so the pill can never hang on "Transcribing…". Esc still cancels sooner.
            let started = await within(Self.finishTimeout) { await startTask?.value }
            guard let self, gen == self.generation else { return }
            guard started else {
                self.fail(self.assetProgress != nil
                    ? "The speech model is still installing. Try again when it finishes."
                    : "On-device transcription didn\u{2019}t start. Try again in a moment.")
                return
            }
            var finished = true
            if let transcriber = capture.preRoll.drain() {
                finished = await within(Self.finishTimeout) { await transcriber.finish() }
            }
            guard gen == self.generation else { return }
            self.capture = nil
            self.assetProgress = nil
            // A transcriber that never finished still leaves its finals and latest guess.
            await self.deliver(finished ? DictationText.join(capture.collector.finals) : capture.collector.text)
        }
    }

    /// Stops and throws away whatever was said.
    func cancel() {
        guard state != .idle else { return }
        generation &+= 1
        tearDown()
        state = .idle
        hud.hide()
    }

    // MARK: Helpers

    /// Shows the system microphone prompt once and says what happened.
    private func requestMicrophone() {
        guard !askingForMicrophone else { return }
        askingForMicrophone = true
        Task { [weak self] in
            let granted = await AVCaptureDevice.requestAccess(for: .audio)
            guard let self else { return }
            self.askingForMicrophone = false
            let key = self.settings.dictationKey.title
            if !granted {
                self.flash(AppError.micDenied.localizedDescription)
            } else if AVCaptureDevice.authorizationStatus(for: .audio) != .authorized {
                // Allowed, but macOS kept no record of it, so it would ask again on every press.
                // That happens to a build without a proper code signature.
                self.flash("macOS didn\u{2019}t save the microphone permission. Reinstall Jarvis Voice from a signed build.")
            } else {
                self.flash("Microphone allowed. Press \(key) to dictate.")
            }
        }
    }

    /// Fetches the speech model in the background, once per launch, so the first dictation
    /// doesn't wait for a download.
    private func prepareAssets() {
        guard assetsTask == nil else { return }
        assetsTask = Task { _ = try? await LocalTranscriber.ensureAssets { _ in } }
    }

    private func startMic(_ capture: Capture) throws {
        let preRoll = capture.preRoll
        try capture.mic.start(deviceUID: settings.inputDeviceUID, voiceProcessing: false) { buffer in
            preRoll.push(buffer)
        }
    }

    private func deliver(_ text: String) async {
        state = .idle
        guard !text.isEmpty else {
            flash("Didn\u{2019}t catch that")
            return
        }
        TextInserter.copy(text)
        guard settings.dictationPaste else {
            flash("Copied")
            return
        }
        hud.hide()
        if !(await TextInserter.paste()) {
            flash("Copied. Allow Accessibility in Settings to paste automatically.")
        }
    }

    private func fail(_ why: String) {
        generation &+= 1
        tearDown()
        state = .idle
        flash(why)
    }

    private func tearDown() {
        assetProgress = nil
        guard let capture else { return }
        self.capture = nil
        capture.startTask?.cancel()
        capture.limitTask?.cancel()
        capture.mic.stop()
        if let transcriber = capture.preRoll.drain(feed: false) {
            Task { await transcriber.finish() }
        }
    }

    private func flash(_ text: String) {
        message = text
        hud.show()
        messageTask?.cancel()
        messageTask = Task { [weak self] in
            try? await Task.sleep(for: .seconds(2.5))
            guard !Task.isCancelled, let self, self.state == .idle else { return }
            self.message = nil
            self.hud.hide()
        }
    }

    private func clearMessage() {
        messageTask?.cancel()
        messageTask = nil
        message = nil
    }
}

/// Runs `operation` and waits at most `timeout` for it. Returns `false` on timeout, leaving the
/// operation to finish on its own.
private func within(_ timeout: Duration, _ operation: @escaping @Sendable () async -> Void) async -> Bool {
    let (results, continuation) = AsyncStream.makeStream(of: Bool.self)
    Task {
        await operation()
        continuation.yield(true)
    }
    let timer = Task {
        try? await Task.sleep(for: timeout)
        continuation.yield(false)
    }
    defer {
        timer.cancel()
        continuation.finish()
    }
    for await finished in results { return finished }
    return false
}

// MARK: - Capture

/// Everything that exists only while one dictation is live.
@MainActor
private final class Capture {
    let mic = MicCapture()
    let preRoll = PreRoll()
    let collector = Collector()
    var startTask: Task<Void, Never>?
    var limitTask: Task<Void, Never>?
    /// Set once the speech model is installed, so late progress reports are dropped.
    var assetsReady = false
}

/// Holds mic buffers until the transcriber has started, so the first word isn't lost, then hands
/// them over. `push` runs on the capture thread, which is also the only thread that feeds the
/// transcriber while capture runs; `drain` is called only after capture has stopped.
private final class PreRoll: @unchecked Sendable {
    /// About 20 s of 4096-frame buffers at 48 kHz; enough for a first-run model install to finish.
    private static let maxQueued = 256

    private let lock = NSLock()
    private var queue: [AVAudioPCMBuffer] = []
    private var transcriber: LocalTranscriber?

    func push(_ buffer: AVAudioPCMBuffer) {
        let (target, pending) = lock.withLock { () -> (LocalTranscriber?, [AVAudioPCMBuffer]) in
            guard let transcriber else {
                if queue.count < Self.maxQueued, let copy = buffer.deepCopy() { queue.append(copy) }
                return (nil, [])
            }
            defer { queue = [] }
            return (transcriber, queue)
        }
        guard let target else { return }
        for queued in pending { target.feed(queued, sessionMs: 0) }
        target.feed(buffer, sessionMs: 0)
    }

    func attach(_ transcriber: LocalTranscriber) {
        lock.withLock { self.transcriber = transcriber }
    }

    /// Detaches the transcriber, first feeding it anything still queued unless `feed` is false.
    func drain(feed: Bool = true) -> LocalTranscriber? {
        let (target, pending) = lock.withLock { () -> (LocalTranscriber?, [AVAudioPCMBuffer]) in
            defer {
                queue = []
                transcriber = nil
            }
            return (transcriber, queue)
        }
        if feed, let target {
            for queued in pending { target.feed(queued, sessionMs: 0) }
        }
        return target
    }
}

/// Collects the transcriber's output from its results task.
private final class Collector: @unchecked Sendable {
    private let lock = NSLock()
    private var finalSegments: [DraftSegment] = []
    private var volatile = ""

    var finals: [DraftSegment] { lock.withLock { finalSegments } }
    /// Finals so far plus the current volatile guess.
    var text: String { lock.withLock { Self.join(finalSegments, volatile) } }

    /// Records `draft` and returns the live text: finals so far plus the current volatile guess.
    func add(_ draft: DraftSegment) -> String {
        lock.withLock { () -> String in
            if draft.final {
                finalSegments.append(draft)
                volatile = ""
            } else {
                volatile = draft.text
            }
            return Self.join(finalSegments, volatile)
        }
    }

    private static func join(_ finals: [DraftSegment], _ volatile: String) -> String {
        [DictationText.join(finals), volatile].filter { !$0.isEmpty }.joined(separator: " ")
    }
}

private extension AVAudioPCMBuffer {
    /// A tap's buffer is only valid during its callback; queued audio needs its own copy.
    func deepCopy() -> AVAudioPCMBuffer? {
        guard let out = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frameLength) else { return nil }
        out.frameLength = frameLength
        let source = UnsafeMutableAudioBufferListPointer(UnsafeMutablePointer(mutating: audioBufferList))
        let destination = UnsafeMutableAudioBufferListPointer(out.mutableAudioBufferList)
        for (src, dst) in zip(source, destination) {
            guard let from = src.mData, let to = dst.mData else { continue }
            memcpy(to, from, Int(min(src.mDataByteSize, dst.mDataByteSize)))
        }
        return out
    }
}
