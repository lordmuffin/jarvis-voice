import AppKit
import AVFoundation
import Foundation
import JarvisLiveKit
import Network
import Observation

/// A local-only session file waiting to be (or already) uploaded.
struct LocalSession: Identifiable, Equatable {
    var header: LocalMarkdown.Header
    var url: URL
    var id: String { header.sessionId }
    var isUploaded: Bool { header.uploadedAs != nil }
}

/// A message ending in exactly one full stop, whatever the underlying error text ends with.
private func sentence(_ text: String) -> String {
    text.hasSuffix(".") ? text : text + "."
}

enum AppError: LocalizedError, Equatable {
    case micDenied
    case noCredentials
    case speechUnavailable(String)
    case audioMissing
    case uploadIncomplete
    case uploadRejected(String)

    var errorDescription: String? {
        switch self {
        case .micDenied:
            "Microphone access is off. Enable it in System Settings \u{203A} Privacy & Security \u{203A} Microphone."
        case .noCredentials:
            "Set the server URL and device token in Settings, or turn on Local-only."
        case .speechUnavailable(let why):
            "On-device transcription is unavailable: \(why)"
        case .audioMissing:
            "The recorded audio for this session is no longer on disk."
        case .uploadIncomplete:
            "The upload was interrupted. It will resume when the server is reachable."
        case .uploadRejected(let why):
            "The server refused the upload: \(why)"
        }
    }
}

/// Owns one recording session end to end: capture, local STT, transport, keep-awake, local files.
@MainActor
@Observable
final class AppModel {
    enum Phase: Equatable { case idle, starting, recording, stopping }
    enum Mode: String, CaseIterable, Identifiable {
        case meeting, solo
        var id: String { rawValue }
        var title: String { self == .meeting ? "Meeting (mic + system)" : "Solo (mic only)" }
    }
    enum Link: Equatable { case idle, connected, reconnecting, offlineLocal, failed(String) }

    // MARK: Observable state

    private(set) var phase: Phase = .idle
    var mode: Mode = .meeting
    /// Follows the network (on when away from home and off NetBird) until the user flips it.
    var localOnly: Bool {
        get { settings.awayMode.localOnly }
        set { settings.awayMode = settings.awayMode.withManual(newValue) }
    }
    private(set) var store = SessionStore()
    private(set) var assetProgress: Double?
    private(set) var systemState: SystemCapture.State = .idle
    private(set) var voiceProcessing = false
    private(set) var notice: String?
    private(set) var markers: [(tMs: Int, label: String)] = []
    private(set) var localSessions: [LocalSession] = []
    private(set) var uploadingSessionId: String?
    private(set) var sessionClock: SessionClock?
    private(set) var sessionIsLocalOnly = false
    /// The running session is local because the server couldn't be reached at the start.
    private(set) var sessionFellBack = false
    /// What the server at `serverInfoURL` said about itself in `/healthz`.
    private var serverInfo: ServerInfo?
    private var serverInfoURL: URL?
    var dismissedSuggestionIDs: Set<String> = []
    /// Whole seconds into the recording, refreshed once a second; drives the menu-bar and panel clocks.
    private(set) var elapsedSeconds = 0

    let settings: SettingsModel
    let dictation: DictationController
    @ObservationIgnored private(set) lazy var panel = PanelController(model: self)
    @ObservationIgnored private let hotkeys = HotKeys()
    @ObservationIgnored private var run: Run?
    @ObservationIgnored private let pathMonitor = NWPathMonitor()
    @ObservationIgnored private var networkCheck: Task<Void, Never>?
    @ObservationIgnored private var serverInfoCheck: Task<Void, Never>?
    @ObservationIgnored private var elapsedTicker: Task<Void, Never>?
    @ObservationIgnored private var autoUploadTask: Task<Void, Never>?

    init() {
        let settings = SettingsModel()
        self.settings = settings
        dictation = DictationController(settings: settings)
        refreshLocalSessions()
        // The test host launches the app; global hotkeys have no place there.
        guard ProcessInfo.processInfo.environment["XCTestConfigurationFilePath"] == nil else { return }
        hotkeys.register { [weak self] action in
            guard let self else { return }
            switch action {
            case .toggleSession: Task { await self.toggle() }
            case .marker: self.addMarker()
            }
        }
        dictation.startMonitoring()
        dictation.requestAccessibilityIfNeeded()
        // Wi-Fi joined or lost, a VPN up or down: work out home / NetBird / away again.
        pathMonitor.pathUpdateHandler = { [weak self] _ in
            Task { @MainActor in self?.recheckNetwork() }
        }
        pathMonitor.start(queue: .main)
    }

    // MARK: Derived

    var isRecording: Bool { phase == .recording }
    var isBusy: Bool { phase == .starting || phase == .stopping }

    var link: Link {
        guard phase != .idle else { return .idle }
        if sessionIsLocalOnly { return .offlineLocal }
        switch store.connection {
        case .connected: return .connected
        case .failed(let why): return .failed(why)
        case .idle, .connecting, .reconnecting: return .reconnecting
        case .ended: return .idle
        }
    }

    var menuBarImage: NSImage {
        // A recording session wins over dictation.
        MenuBarIcon.image(MenuBarIconSpec.make(
            isRecording: isRecording,
            isDictating: dictation.isActive,
            elapsedSeconds: elapsedSeconds,
            channel: serverChannel,
            localOnly: sessionIsLocalOnly
        ))
    }

    /// The server's own word once it has answered at the current address, else a guess from the
    /// address; nil without a valid one.
    var serverChannel: ServerChannel? {
        guard let base = settings.serverBaseURL else { return nil }
        return reportedServer(at: base)?.channel ?? ServerChannel(guessingFrom: base)
    }

    /// The server's release version, once it has said.
    var serverVersion: String? {
        settings.serverBaseURL.flatMap { reportedServer(at: $0)?.version }
    }

    private func reportedServer(at base: URL) -> ServerInfo? {
        serverInfoURL == base ? serverInfo : nil
    }

    // MARK: Network

    /// Checks where the Mac is, once path updates settle; a newer change cancels a check still
    /// waiting on the server.
    func recheckNetwork() {
        // A local-only session sends nothing, not even a health check (see `EgressGate`); the
        // network is looked at again when it ends.
        guard !EgressGate.shared.isBlocking else { return }
        refreshServerInfo()
        networkCheck?.cancel()
        networkCheck = Task { [weak self] in
            try? await Task.sleep(for: .milliseconds(750))
            guard !Task.isCancelled, let self else { return }
            guard settings.awayMode.enabled, settings.hasCredentials, let base = settings.serverBaseURL else { return }
            let place = await NetworkPlaceDetector.detect(baseURL: base)
            guard !Task.isCancelled else { return }
            settings.awayMode = settings.awayMode.withPlace(place)
        }
    }

    /// Asks the server which channel and version it is. Keeps the last answer while the server
    /// can't be reached, so the tag doesn't flicker back to a guess away from home.
    private func refreshServerInfo() {
        serverInfoCheck?.cancel()
        serverInfoCheck = Task { [weak self] in
            try? await Task.sleep(for: .milliseconds(750))
            guard !Task.isCancelled, let self, let base = settings.serverBaseURL else { return }
            guard let info = await ServerInfo.fetch(baseURL: base),
                  !Task.isCancelled, settings.serverBaseURL == base else { return }
            serverInfoURL = base
            serverInfo = info
            // The server answered: recordings made while it was down can go now.
            startAutoUpload()
        }
    }

    /// Uploads fallback recordings one at a time; stops at the first failure so a flaky server
    /// isn't hammered on every network change.
    private func startAutoUpload() {
        guard autoUploadTask == nil, settings.hasCredentials else { return }
        autoUploadTask = Task { [weak self] in
            await self?.uploadFallbackSessions()
            self?.autoUploadTask = nil
        }
    }

    private func uploadFallbackSessions() async {
        refreshLocalSessions()
        for session in localSessions where session.header.autoUpload && !session.isUploaded {
            guard phase == .idle else { return }
            await upload(session)
            guard localSessions.first(where: { $0.id == session.id })?.isUploaded == true else { return }
        }
    }

    // MARK: Session control

    func toggle() async {
        switch phase {
        case .idle: await start()
        case .recording: await stop()
        case .starting, .stopping: break
        }
    }

    func start() async {
        guard phase == .idle else { return }
        phase = .starting
        notice = nil
        systemState = .idle
        markers = []
        dismissedSuggestionIDs = []
        let freshStore = SessionStore()
        store = freshStore
        let localOnly = self.localOnly
        let mode = self.mode
        var built: Run?
        // Shut the network before anything else happens, so not even a failed start leaks.
        if localOnly { EgressGate.shared.beginLocalSession() }
        do {
            guard await AVCaptureDevice.requestAccess(for: .audio) else { throw AppError.micDenied }
            let run = try await prepare(localOnly: localOnly, mode: mode, store: freshStore)
            built = run
            // Before capture starts, from the session itself, so the indicator never shows the last one's mode.
            sessionIsLocalOnly = run.localOnly
            sessionFellBack = run.fellBack
            try await begin(run)
            self.run = run
            sessionClock = run.clock
            phase = .recording
            startElapsedTicker()
            panel.show()
        } catch {
            if let built { await tearDown(built, writeFile: false) }
            if localOnly { endLocalSession() }
            notice = error.localizedDescription
            phase = .idle
            sessionClock = nil
            sessionIsLocalOnly = false
            sessionFellBack = false
        }
    }

    func stop() async {
        guard phase == .recording, let run else { return }
        phase = .stopping
        elapsedTicker?.cancel()
        await tearDown(run, writeFile: run.localOnly)
        if run.localOnly { endLocalSession() }
        self.run = nil
        phase = .idle
        elapsedSeconds = 0
        refreshLocalSessions()
    }

    /// Opens the network again and looks at where the Mac is, which was not checked meanwhile.
    private func endLocalSession() {
        EgressGate.shared.endLocalSession()
        recheckNetwork()
    }

    private func startElapsedTicker() {
        elapsedTicker?.cancel()
        elapsedSeconds = 0
        elapsedTicker = Task { [weak self] in
            while !Task.isCancelled {
                guard let self, self.isRecording else { return }
                self.elapsedSeconds = (self.sessionClock?.elapsedMs() ?? 0) / 1000
                try? await Task.sleep(for: .seconds(1))
            }
        }
    }

    func addMarker() {
        guard isRecording, let run else { return }
        let tMs = run.clock.elapsedMs()
        let label = "Marker \(markers.count + 1)"
        markers.append((tMs, label))
        if let connection = run.connection {
            Task { await connection.sendMarker(Marker(tMs: tMs, label: label)) }
        }
    }

    // MARK: Setup

    private func prepare(localOnly requestedLocalOnly: Bool, mode: Mode, store: SessionStore) async throws -> Run {
        let channels: [Channel] = mode == .meeting ? [.mic, .system] : [.mic]
        var sessionId = UUID().uuidString.lowercased()
        var api: LiveAPIClient?
        var base: URL?
        var localOnly = requestedLocalOnly
        var fellBack = false
        if !localOnly {
            guard settings.hasCredentials, let url = settings.serverBaseURL else { throw AppError.noCredentials }
            let token = settings.deviceToken
            let client = LiveAPIClient(baseURL: url, bearerToken: { token })
            do {
                let created = try await client.createSession(
                    SessionCreateRequest(mode: mode == .meeting ? .meeting : .solo, channels: channels)
                )
                sessionId = created.id
                api = client
                base = url
            } catch where StartFallback.shouldFallBack(error) {
                // The server is down: still record the meeting, and upload it when the server is back.
                localOnly = true
                fellBack = true
            }
        }

        let outbox = try Outbox(sessionId: sessionId)
        var connection: LiveConnection?
        if let api, let base {
            connection = LiveConnection(
                configuration: .init(
                    apiBaseURL: base, sessionId: sessionId,
                    device: "mac:\(ProcessInfo.processInfo.hostName)"
                ),
                outbox: outbox,
                ticketProvider: api.producerTicketProvider(sessionId: sessionId)
            )
        }
        return Run(
            sessionId: sessionId, channels: channels, mode: mode, localOnly: localOnly,
            fellBack: fellBack, outbox: outbox, connection: connection, store: store
        )
    }

    private func begin(_ run: Run) async throws {
        // On-device STT assets (first run only), with progress in the menu.
        var sttAvailable = true
        do {
            assetProgress = 0
            try await LocalTranscriber.ensureAssets { [weak self] fraction in
                Task { @MainActor in self?.assetProgress = fraction }
            }
            assetProgress = nil
        } catch {
            assetProgress = nil
            sttAvailable = false
            // A fallback recording is uploaded and transcribed by the server later, so it needs no drafts.
            if run.localOnly && !run.fellBack { throw AppError.speechUnavailable(error.localizedDescription) }
            notice = "On-device drafts are off: \(sentence(error.localizedDescription)) The server still transcribes."
        }

        let onError: @Sendable (String) -> Void = { [weak self] message in
            Task { @MainActor in self?.notice = message }
        }

        // Drafts → store (and the wire) in order, on the main actor.
        let emissions = run.emissions
        let connection = run.connection
        let store = run.store
        run.emissionTask = Task { @MainActor [weak run] in
            for await emission in emissions.stream {
                store.applyDraft(emission.draft)
                if emission.draft.final { run?.finals.append(emission.draft) }
                if emission.sendToServer, let connection { await connection.sendDraft(emission.draft) }
            }
        }

        // Frames → outbox / connection, one consumer so sequence numbers follow capture order.
        let sink = run.sink
        let outbox = run.outbox
        run.consumer = Task {
            for await frame in sink.stream {
                do {
                    if let connection {
                        try await connection.enqueueAudio(channel: frame.channel, payload: frame.payload, tMs: frame.tMs)
                    } else {
                        _ = try await outbox.append(channel: frame.channel, payload: frame.payload, tMs: frame.tMs)
                    }
                } catch {
                    onError("Could not store audio: \(sentence(error.localizedDescription))")
                }
            }
        }

        for channel in run.channels {
            var transcriber: LocalTranscriber?
            if sttAvailable {
                let t = LocalTranscriber(channel: channel) { emissions.continuation.yield($0) }
                try await t.start()
                transcriber = t
                run.transcribers.append(t)
            }
            run.pipelines[channel] = ChannelPipeline(
                channel: channel, clock: run.clock, sink: sink.continuation,
                transcriber: transcriber, onError: onError
            )
        }

        if let connection {
            run.store.attach(connection)
            await connection.start()
        }

        // Mic first, then the system tap.
        guard let micPipeline = run.pipelines[.mic] else { return }
        try run.mic.start(deviceUID: settings.inputDeviceUID) { buffer in micPipeline.process(buffer) }
        voiceProcessing = run.mic.voiceProcessingActive

        if run.channels.contains(.system), let systemPipeline = run.pipelines[.system] {
            let system = SystemCapture()
            run.system = system
            system.start(
                handler: { buffer in systemPipeline.process(buffer) },
                onState: { [weak self] state in Task { @MainActor in self?.systemState = state } }
            )
        }

        run.activity = ProcessInfo.processInfo.beginActivity(
            options: [.userInitiated, .idleSystemSleepDisabled], reason: "Jarvis Voice is recording"
        )
    }

    // MARK: Teardown

    private func tearDown(_ run: Run, writeFile: Bool) async {
        run.mic.stop()
        run.system?.stop()
        for pipeline in run.pipelines.values { pipeline.finish() }
        run.sink.continuation.finish()
        await run.consumer?.value
        for transcriber in run.transcribers { await transcriber.finish() }
        run.emissions.continuation.finish()
        await run.emissionTask?.value
        if let connection = run.connection {
            await connection.end()
            run.store.detach()
        }
        await run.outbox.close()
        if let activity = run.activity { ProcessInfo.processInfo.endActivity(activity) }
        run.activity = nil
        systemState = .idle
        if writeFile { writeLocalFile(for: run) }
    }

    private func writeLocalFile(for run: Run) {
        let header = LocalMarkdown.Header(
            sessionId: run.sessionId,
            mode: run.mode == .meeting ? .meeting : .solo,
            channels: run.channels, started: run.startedAt, uploadedAs: nil, autoUpload: run.fellBack
        )
        let lines = run.finals.map { LocalMarkdown.Line(channel: $0.channel, startMs: $0.startMs, text: $0.text) }
        let text = LocalMarkdown.render(header: header, lines: lines, markers: markers)
        do {
            try FileManager.default.createDirectory(at: LocalMarkdown.directory, withIntermediateDirectories: true)
            let url = LocalMarkdown.directory.appending(path: LocalMarkdown.fileName(for: run.startedAt))
            try text.write(to: url, atomically: true, encoding: .utf8)
            notice = "Saved \(url.lastPathComponent) to Documents/Jarvis Live."
        } catch {
            notice = "Could not save the local transcript: \(sentence(error.localizedDescription))"
        }
    }

    // MARK: Local sessions

    func refreshLocalSessions() {
        let urls = (try? FileManager.default.contentsOfDirectory(
            at: LocalMarkdown.directory, includingPropertiesForKeys: nil
        )) ?? []
        localSessions = urls.filter { $0.pathExtension == "md" }.compactMap { url in
            guard let text = try? String(contentsOf: url, encoding: .utf8),
                  let header = LocalMarkdown.parseHeader(text) else { return nil }
            return LocalSession(header: header, url: url)
        }.sorted { $0.header.started > $1.header.started }
    }

    /// Replays a local-only session's recorded audio to the server for full processing.
    func upload(_ session: LocalSession) async {
        guard uploadingSessionId == nil, !session.isUploaded else { return }
        guard settings.hasCredentials, let base = settings.serverBaseURL else {
            notice = AppError.noCredentials.localizedDescription
            return
        }
        uploadingSessionId = session.id
        defer { uploadingSessionId = nil }
        do {
            let token = settings.deviceToken
            let api = LiveAPIClient(baseURL: base, bearerToken: { token })
            let root = Outbox.defaultRoot
            var text = try String(contentsOf: session.url, encoding: .utf8)
            // A retry resumes the server session an earlier attempt made; only the first attempt creates one.
            let serverId: String
            if let existing = session.header.serverSessionId {
                serverId = existing
            } else {
                serverId = try await api.createSession(
                    SessionCreateRequest(mode: session.header.mode, channels: session.header.channels)
                ).id
                // Written before any audio moves, so an interruption from here on can be resumed.
                text = LocalMarkdown.settingServerSession(text, id: serverId)
                try text.write(to: session.url, atomically: true, encoding: .utf8)
            }
            try UploadHandoff.relocate(root: root, from: session.header.sessionId, to: serverId)

            let outbox = try Outbox(sessionId: serverId, root: root)
            let connection = LiveConnection(
                configuration: .init(
                    apiBaseURL: base, sessionId: serverId, device: "mac:\(ProcessInfo.processInfo.hostName)"
                ),
                outbox: outbox,
                ticketProvider: api.producerTicketProvider(sessionId: serverId)
            )
            await connection.start()
            await connection.end()
            let finished = await connection.state
            await outbox.close()

            // `end()` also returns when it times out or the server is gone; only a connection that
            // ended means the server has all of it. Anything else stays pending and resumes.
            switch finished {
            case .ended: break
            case .failed(let why): throw AppError.uploadRejected(why)
            default: throw AppError.uploadIncomplete
            }
            try LocalMarkdown.markUploaded(text, serverSessionId: serverId)
                .write(to: session.url, atomically: true, encoding: .utf8)
            notice = "Uploaded \(session.url.lastPathComponent) for full processing."
        } catch {
            notice = "Upload failed: \(sentence(error.localizedDescription))"
        }
        refreshLocalSessions()
    }
}

// MARK: - Run

/// Everything that exists only while a session is live.
@MainActor
private final class Run {
    struct Emissions {
        let stream: AsyncStream<LocalTranscriber.Emission>
        let continuation: AsyncStream<LocalTranscriber.Emission>.Continuation
    }
    struct Sink {
        let stream: AsyncStream<FrameItem>
        let continuation: AsyncStream<FrameItem>.Continuation
    }

    let sessionId: String
    let channels: [Channel]
    let mode: AppModel.Mode
    let localOnly: Bool
    /// Local because the server couldn't be reached at the start (see `StartFallback`).
    let fellBack: Bool
    let outbox: Outbox
    let connection: LiveConnection?
    let store: SessionStore
    let clock = SessionClock()
    let startedAt = Date()
    let mic = MicCapture()
    var system: SystemCapture?
    let sink: Sink
    let emissions: Emissions
    var consumer: Task<Void, Never>?
    var emissionTask: Task<Void, Never>?
    var pipelines: [Channel: ChannelPipeline] = [:]
    var transcribers: [LocalTranscriber] = []
    var finals: [DraftSegment] = []
    var activity: NSObjectProtocol?

    init(
        sessionId: String, channels: [Channel], mode: AppModel.Mode, localOnly: Bool, fellBack: Bool,
        outbox: Outbox, connection: LiveConnection?, store: SessionStore
    ) {
        self.sessionId = sessionId
        self.channels = channels
        self.mode = mode
        self.localOnly = localOnly
        self.fellBack = fellBack
        self.outbox = outbox
        self.connection = connection
        self.store = store
        let (frames, frameContinuation) = AsyncStream.makeStream(of: FrameItem.self, bufferingPolicy: .unbounded)
        sink = Sink(stream: frames, continuation: frameContinuation)
        let (drafts, draftContinuation) = AsyncStream.makeStream(
            of: LocalTranscriber.Emission.self, bufferingPolicy: .unbounded
        )
        emissions = Emissions(stream: drafts, continuation: draftContinuation)
    }
}
