import Foundation

public enum ConnectionState: Equatable, Sendable {
    case idle
    case connecting
    case connected
    /// Waiting to retry after a failure; `attempt` is 1 for the first retry.
    case reconnecting(attempt: Int)
    /// `end()` completed.
    case ended
    /// Unrecoverable (e.g. the server rejected our credentials).
    case failed(String)
}

/// A resumable producer connection for one session.
///
/// Audio enters through `enqueueAudio` (or directly via the shared `Outbox`, followed by
/// `kick()`); the outbox is the only record of what was captured, and only the server's cumulative
/// `ack` frees data. On every (re)connect the connection fetches a fresh ticket, sends `hello` with
/// the outbox's acked seqs as `resume`, applies the server's `hello_ack`, then replays everything
/// still unacked before streaming new frames.
public actor LiveConnection {
    public struct Configuration: Sendable {
        public var apiBaseURL: URL
        public var sessionId: String
        public var device: String
        /// The send loop pauses while more than this much sent audio is unacked.
        public var maxUnackedAudioMs: Int
        public var reconnect: ReconnectPolicy
        /// How long to wait for `hello_ack` after sending `hello`.
        public var handshakeTimeout: Duration
        /// After `end` is sent and acked, how long to wait for the server to finish (`final_note`/close).
        public var endGracePeriod: Duration

        public init(
            apiBaseURL: URL,
            sessionId: String,
            device: String,
            maxUnackedAudioMs: Int = 5_000,
            reconnect: ReconnectPolicy = ReconnectPolicy(),
            handshakeTimeout: Duration = .seconds(10),
            endGracePeriod: Duration = .seconds(10)
        ) {
            self.apiBaseURL = apiBaseURL
            self.sessionId = sessionId
            self.device = device
            self.maxUnackedAudioMs = maxUnackedAudioMs
            self.reconnect = reconnect
            self.handshakeTimeout = handshakeTimeout
            self.endGracePeriod = endGracePeriod
        }
    }

    /// Server events other than `ack`/`hello_ack`, which the connection consumes itself.
    public nonisolated let events: AsyncStream<ServerEvent>
    public nonisolated let states: AsyncStream<ConnectionState>
    public private(set) var state: ConnectionState = .idle

    private let configuration: Configuration
    private let outbox: Outbox
    private let ticketProvider: @Sendable () async throws -> String
    private let urlSession: URLSession
    private let eventContinuation: AsyncStream<ServerEvent>.Continuation
    private let stateContinuation: AsyncStream<ConnectionState>.Continuation

    private var runTask: Task<Void, Never>?
    private var failureCount = 0
    private var pendingControl: [ClientMessage] = []
    private var endRequested = false
    private var endSent = false
    private var finished = false
    private var endWaiters: [CheckedContinuation<Void, Never>] = []
    private var wakeWaiter: CheckedContinuation<Void, Never>?
    private var wakePending = false

    private static let maxQueuedControlMessages = 256

    public init(
        configuration: Configuration,
        outbox: Outbox,
        ticketProvider: @escaping @Sendable () async throws -> String,
        urlSession: URLSession = URLSession(configuration: .ephemeral)
    ) {
        self.configuration = configuration
        self.outbox = outbox
        self.ticketProvider = ticketProvider
        self.urlSession = urlSession
        (events, eventContinuation) = AsyncStream.makeStream(of: ServerEvent.self, bufferingPolicy: .unbounded)
        (states, stateContinuation) = AsyncStream.makeStream(of: ConnectionState.self, bufferingPolicy: .unbounded)
    }

    // MARK: - Public API

    /// Starts connecting. Idempotent.
    public func start() {
        guard runTask == nil, !finished else { return }
        runTask = Task { await self.run() }
    }

    /// Stores a captured frame in the outbox and wakes the send loop. Returns the stored frame.
    @discardableResult
    public func enqueueAudio(channel: Channel, payload: Data, tMs: UInt32) async throws -> AudioFrame {
        let frame = try await outbox.append(channel: channel, payload: payload, tMs: tMs)
        wake()
        return frame
    }

    /// Wakes the send loop after frames were appended to the outbox directly.
    public func kick() { wake() }

    public func sendDraft(_ draft: DraftSegment) {
        enqueueControl(.draftSegment(draft))
    }

    public func sendMarker(_ marker: Marker) {
        enqueueControl(.marker(marker))
    }

    /// Flushes all audio, sends `end`, waits for the server to ack everything (and, briefly, for
    /// `final_note`), then closes. Returns once the connection has finished or failed.
    public func end() async {
        guard !finished else { return }
        endRequested = true
        start()
        wake()
        await withCheckedContinuation { endWaiters.append($0) }
    }

    /// Drops the connection without ending the session; the outbox stays resumable.
    public func stop() {
        runTask?.cancel()
        runTask = nil
        if !finished { finish(.idle) }
    }

    // MARK: - Run loop

    private func run() async {
        while !Task.isCancelled, !finished {
            setState(failureCount == 0 ? .connecting : .reconnecting(attempt: failureCount))
            do {
                try await runOnce()
                finish(.ended)
                return
            } catch is CancellationError {
                return
            } catch let LiveAPIError.http(status, body) where [401, 403, 404].contains(status) {
                finish(.failed("HTTP \(status): \(body)"))
                return
            } catch {
                // Transient: fall through to backoff and retry.
            }
            if Task.isCancelled || finished { return }
            let delay = configuration.reconnect.delay(attempt: failureCount)
            failureCount += 1
            setState(.reconnecting(attempt: failureCount))
            do {
                try await Task.sleep(for: .seconds(delay))
            } catch {
                return
            }
        }
    }

    private func runOnce() async throws {
        let ticket = try await ticketProvider()
        let task = urlSession.webSocketTask(with: streamURL(ticket: ticket))
        task.resume()
        endSent = false  // `end` is re-sent on a new connection if it was not fully acked.
        try await withTaskCancellationHandler {
            try await converse(on: task)
        } onCancel: {
            // `receive()` does not observe task cancellation; closing the socket unblocks it.
            task.cancel(with: .goingAway, reason: nil)
        }
    }

    private func converse(on task: URLSessionWebSocketTask) async throws {
        defer { task.cancel(with: .normalClosure, reason: nil) }

        // Handshake: hello (with resume from the outbox) → hello_ack.
        let resume = await outbox.ackedSeqs()
        try await send(.hello(Hello(device: configuration.device, resume: resume)), on: task)
        let helloAck = try await receiveHelloAck(on: task)
        for channel in Channel.allCases {
            if let seq = helloAck.acked[channel] { try await outbox.markAcked(channel, seq: seq) }
        }
        failureCount = 0
        setState(.connected)

        // Everything past the (now authoritative) ack gets replayed by the send loop from seq acked+1.
        var nextToSend: [Channel: UInt32] = [:]
        for channel in Channel.allCases {
            nextToSend[channel] = await outbox.ackedSeq(for: channel).map { $0 &+ 1 } ?? 0
        }
        let start = nextToSend

        try await withThrowingTaskGroup(of: Void.self) { group in
            group.addTask { try await self.receiveLoop(on: task) }
            group.addTask {
                try await self.sendLoop(on: task, nextToSend: start)
                // `end` is sent and fully acked; give the server a moment to emit final_note / close.
                try await Task.sleep(for: self.configuration.endGracePeriod)
            }
            // Whichever finishes first decides the outcome: a throw means reconnect, a normal
            // return means the session ended. Close the socket first so the loser, which may be
            // blocked in `receive()`, can finish and the group can exit.
            do {
                _ = try await group.next()
            } catch {
                task.cancel(with: .goingAway, reason: nil)
                group.cancelAll()
                throw error
            }
            task.cancel(with: .normalClosure, reason: nil)
            group.cancelAll()
        }
    }

    private func streamURL(ticket: String) -> URL {
        var components = URLComponents(
            url: configuration.apiBaseURL.appending(path: "v1/sessions/\(configuration.sessionId)/stream"),
            resolvingAgainstBaseURL: false
        )!
        switch components.scheme {
        case "https": components.scheme = "wss"
        case "http": components.scheme = "ws"
        default: break
        }
        components.queryItems = [URLQueryItem(name: "ticket", value: ticket)]
        return components.url!
    }

    // MARK: - Receive

    private func receiveHelloAck(on task: URLSessionWebSocketTask) async throws -> HelloAck {
        try await withThrowingTaskGroup(of: HelloAck.self) { group in
            group.addTask {
                while true {
                    let message = try await task.receive()
                    if case .helloAck(let ack)? = Self.decodeEvent(message) { return ack }
                    // Anything before hello_ack is ignored.
                }
            }
            group.addTask {
                try await Task.sleep(for: self.configuration.handshakeTimeout)
                task.cancel(with: .goingAway, reason: nil)
                throw URLError(.timedOut)
            }
            defer { group.cancelAll() }
            return try await group.next()!
        }
    }

    private func receiveLoop(on task: URLSessionWebSocketTask) async throws {
        while true {
            let message: URLSessionWebSocketTask.Message
            do {
                message = try await task.receive()
            } catch {
                // Once `end` is out, the server closing the socket is the normal way to finish,
                // but only if it had acked everything first; otherwise reconnect and replay.
                if endSent, await isFullyAcked() { return }
                throw error
            }
            guard let event = Self.decodeEvent(message) else { continue }
            switch event {
            case .ack(let ack):
                try await outbox.markAcked(ack.channel, seq: ack.seq)
                wake()
            case .helloAck:
                continue
            case .finalNote:
                eventContinuation.yield(event)
                if endSent, await isFullyAcked() { return }
            default:
                eventContinuation.yield(event)
            }
        }
    }

    private nonisolated static func decodeEvent(_ message: URLSessionWebSocketTask.Message) -> ServerEvent? {
        switch message {
        case .string(let text):
            return try? JSONDecoder().decode(ServerEvent.self, from: Data(text.utf8))
        case .data:
            return nil  // The server never sends binary frames to a producer.
        @unknown default:
            return nil
        }
    }

    // MARK: - Send

    private func sendLoop(on task: URLSessionWebSocketTask, nextToSend initial: [Channel: UInt32]) async throws {
        var nextToSend = initial
        while true {
            try Task.checkCancellation()
            var progressed = false

            while !pendingControl.isEmpty {
                let message = pendingControl.removeFirst()
                do {
                    try await send(message, on: task)
                } catch {
                    if case .end = message {} else { pendingControl.insert(message, at: 0) }
                    throw error
                }
                progressed = true
            }

            for channel in Channel.allCases {
                let next = nextToSend[channel] ?? 0
                var inFlightMs = next == 0 ? 0 : await outbox.unackedDurationMs(channel, through: next - 1)
                guard inFlightMs <= configuration.maxUnackedAudioMs else { continue }  // backpressure
                let frames = try await outbox.unacked(channel, from: next, limit: 16)
                for frame in frames {
                    if inFlightMs > configuration.maxUnackedAudioMs { break }
                    try await task.send(.data(try frame.encoded()))
                    nextToSend[channel] = frame.seq &+ 1
                    inFlightMs += frame.durationMs
                    progressed = true
                }
            }

            if endRequested, !progressed {
                var allSent = true
                var allAcked = true
                for channel in Channel.allCases {
                    let pendingFrames = try await outbox.unacked(channel, from: nextToSend[channel] ?? 0, limit: 1)
                    if !pendingFrames.isEmpty { allSent = false }
                    if await outbox.unackedDurationMs(channel) > 0 { allAcked = false }
                }
                if allSent, pendingControl.isEmpty {
                    if !endSent {
                        try await send(.end, on: task)
                        endSent = true
                    }
                    if allAcked { return }
                }
            }

            if !progressed { await waitForWake() }
        }
    }

    private func isFullyAcked() async -> Bool {
        for channel in Channel.allCases where await outbox.unackedDurationMs(channel) > 0 {
            return false
        }
        return true
    }

    private func send(_ message: ClientMessage, on task: URLSessionWebSocketTask) async throws {
        let data = try JSONEncoder().encode(message)
        try await task.send(.string(String(decoding: data, as: UTF8.self)))
    }

    private func enqueueControl(_ message: ClientMessage) {
        guard !finished else { return }
        pendingControl.append(message)
        if pendingControl.count > Self.maxQueuedControlMessages {
            // Offline for a long time: shed the oldest draft before anything else.
            if let index = pendingControl.firstIndex(where: {
                if case .draftSegment = $0 { true } else { false }
            }) {
                pendingControl.remove(at: index)
            } else {
                pendingControl.removeFirst()
            }
        }
        wake()
    }

    // MARK: - State

    private func setState(_ new: ConnectionState) {
        guard state != new else { return }
        state = new
        stateContinuation.yield(new)
    }

    private func finish(_ terminal: ConnectionState) {
        finished = true
        setState(terminal)
        eventContinuation.finish()
        stateContinuation.finish()
        let waiters = endWaiters
        endWaiters = []
        waiters.forEach { $0.resume() }
        wake()
    }

    // MARK: - Wake signal

    private func wake() {
        if let waiter = wakeWaiter {
            wakeWaiter = nil
            waiter.resume()
        } else {
            wakePending = true
        }
    }

    private func waitForWake() async {
        if wakePending {
            wakePending = false
            return
        }
        await withTaskCancellationHandler {
            await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
                if Task.isCancelled {
                    continuation.resume()
                } else {
                    wakeWaiter = continuation
                }
            }
        } onCancel: {
            Task { await self.wake() }
        }
    }
}
