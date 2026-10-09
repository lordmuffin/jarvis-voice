import AppKit
import AVFoundation
import Foundation
import JarvisLiveKit
import Observation

/// A local-only session file waiting to be (or already) uploaded.
struct LocalSession: Identifiable, Equatable {
    var header: LocalMarkdown.Header
    var url: URL
    var id: String { header.sessionId }
    var isUploaded: Bool { header.uploadedAs != nil }
}

enum AppError: LocalizedError {
    case micDenied
    case noCredentials
    case speechUnavailable(String)
    case audioMissing

    var errorDescription: String? {
        switch self {
        case .micDenied:
            "Microphone access is off. Enable it in System Settings \u{203A} Privacy & Security \u{203A} Microphone."
        case .noCredentials:
            "Set the server URL and device token in Settings, or turn on Local only."
        case .speechUnavailable(let why):
            "On-device transcription is unavailable: \(why)"
        case .audioMissing:
            "The recorded audio for this session is no longer on disk."
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
    var localOnly = false
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
    var dismissedSuggestionIDs: Set<String> = []

    let settings = SettingsModel()
    @ObservationIgnored private(set) lazy var panel = PanelController(model: self)
    @ObservationIgnored private let hotkeys = HotKeys()
    @ObservationIgnored private var run: Run?

    init() {
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
        let name = isRecording ? "record.circle.fill" : "waveform"
        let image = NSImage(systemSymbolName: name, accessibilityDescription: "Jarvis Voice")
        guard isRecording, let image else {
            image?.isTemplate = true
            return image ?? NSImage()
        }
        let configured = image.withSymbolConfiguration(.init(paletteColors: [.systemRed]))
        configured?.isTemplate = false
        return configured ?? image
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
        do {
            guard await AVCaptureDevice.requestAccess(for: .audio) else { throw AppError.micDenied }
            let run = try await prepare(localOnly: localOnly, mode: mode, store: freshStore)
            built = run
            try await begin(run)
            self.run = run
            sessionClock = run.clock
            sessionIsLocalOnly = localOnly
            phase = .recording
            panel.show()
        } catch {
            if let built { await tearDown(built, writeFile: false) }
            notice = error.localizedDescription
            phase = .idle
            sessionClock = nil
        }
    }

    func stop() async {
        guard phase == .recording, let run else { return }
        phase = .stopping
        await tearDown(run, writeFile: run.localOnly)
        self.run = nil
        phase = .idle
        refreshLocalSessions()
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

    private func prepare(localOnly: Bool, mode: Mode, store: SessionStore) async throws -> Run {
        let channels: [Channel] = mode == .meeting ? [.mic, .system] : [.mic]
        var sessionId = UUID().uuidString.lowercased()
        var api: LiveAPIClient?
        var base: URL?
        if !localOnly {
            guard settings.hasCredentials, let url = settings.serverBaseURL else { throw AppError.noCredentials }
            let token = settings.deviceToken
            let client = LiveAPIClient(baseURL: url, bearerToken: { token })
            let created = try await client.createSession(
                SessionCreateRequest(mode: mode == .meeting ? .meeting : .solo, channels: channels)
            )
            sessionId = created.id
            api = client
            base = url
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
            outbox: outbox, connection: connection, store: store
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
            if run.localOnly { throw AppError.speechUnavailable(error.localizedDescription) }
            notice = "On-device drafts are off: \(error.localizedDescription) The server still transcribes."
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
                    onError("Could not store audio: \(error.localizedDescription)")
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
            channels: run.channels, started: run.startedAt, uploadedAs: nil
        )
        let lines = run.finals.map { LocalMarkdown.Line(channel: $0.channel, startMs: $0.startMs, text: $0.text) }
        let text = LocalMarkdown.render(header: header, lines: lines, markers: markers)
        do {
            try FileManager.default.createDirectory(at: LocalMarkdown.directory, withIntermediateDirectories: true)
            let url = LocalMarkdown.directory.appending(path: LocalMarkdown.fileName(for: run.startedAt))
            try text.write(to: url, atomically: true, encoding: .utf8)
            notice = "Saved \(url.lastPathComponent) to Documents/Jarvis Live."
        } catch {
            notice = "Could not save the local transcript: \(error.localizedDescription)"
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
            let created = try await api.createSession(
                SessionCreateRequest(mode: session.header.mode, channels: session.header.channels)
            )
            // The outbox is keyed by session id: move the recorded audio under the server's id.
            let root = Outbox.defaultRoot
            let source = root.appending(path: session.header.sessionId, directoryHint: .isDirectory)
            let destination = root.appending(path: created.id, directoryHint: .isDirectory)
            guard FileManager.default.fileExists(atPath: source.path) else { throw AppError.audioMissing }
            try FileManager.default.moveItem(at: source, to: destination)

            let outbox = try Outbox(sessionId: created.id, root: root)
            let connection = LiveConnection(
                configuration: .init(
                    apiBaseURL: base, sessionId: created.id, device: "mac:\(ProcessInfo.processInfo.hostName)"
                ),
                outbox: outbox,
                ticketProvider: api.producerTicketProvider(sessionId: created.id)
            )
            await connection.start()
            await connection.end()
            await outbox.close()

            let text = try String(contentsOf: session.url, encoding: .utf8)
            try LocalMarkdown.markUploaded(text, serverSessionId: created.id)
                .write(to: session.url, atomically: true, encoding: .utf8)
            notice = "Uploaded \(session.url.lastPathComponent) for full processing."
        } catch {
            notice = "Upload failed: \(error.localizedDescription)"
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
        sessionId: String, channels: [Channel], mode: AppModel.Mode, localOnly: Bool,
        outbox: Outbox, connection: LiveConnection?, store: SessionStore
    ) {
        self.sessionId = sessionId
        self.channels = channels
        self.mode = mode
        self.localOnly = localOnly
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
