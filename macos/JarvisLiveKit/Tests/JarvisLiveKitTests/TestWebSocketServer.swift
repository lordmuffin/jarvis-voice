import Foundation
import Network
@testable import JarvisLiveKit

/// A minimal in-process Jarvis Live server: accepts WebSocket connections on loopback, answers
/// `hello` with `hello_ack`, stores audio frames in order and acks them cumulatively.
///
/// All mutable state is confined to `queue`.
final class TestWebSocketServer: @unchecked Sendable {
    struct Snapshot: Sendable {
        var hellos: [Hello] = []
        /// Every frame seq received, in arrival order, per channel (duplicates included).
        var received: [Channel: [UInt32]] = [:]
        var duplicates = 0
        var gaps = 0
        var markers: [Marker] = []
        var drafts: [DraftSegment] = []
        var endReceived = false
        var connectionCount = 0
        var acked = ChannelSeqs()
    }

    private let queue = DispatchQueue(label: "jarvis.test-ws-server")
    private var listener: NWListener?
    private var connections: [NWConnection] = []
    private var snapshot = Snapshot()
    private var holdAcks = false
    private var finalNoteOnEnd: FinalNote? = FinalNote(path: "Meetings/test.md", title: "Test")
    private var closeAfterEnd = true
    private let session: String

    private(set) var port: UInt16 = 0

    init(session: String = "3f2b8c1e-5d4a-4b7e-9a10-2c6d8e9f0a1b") {
        self.session = session
    }

    // MARK: Lifecycle

    func start() async throws {
        let parameters = NWParameters.tcp
        let websocket = NWProtocolWebSocket.Options()
        websocket.autoReplyPing = true
        parameters.defaultProtocolStack.applicationProtocols.insert(websocket, at: 0)
        // Loopback only.
        parameters.requiredInterfaceType = .loopback

        let listener = try NWListener(using: parameters, on: .any)
        listener.newConnectionHandler = { [weak self] connection in self?.accept(connection) }
        self.listener = listener

        port = try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<UInt16, Error>) in
            let resumed = Mutex(false)
            listener.stateUpdateHandler = { state in
                switch state {
                case .ready:
                    if !resumed.withLock({ let old = $0; $0 = true; return old }) {
                        continuation.resume(returning: listener.port?.rawValue ?? 0)
                    }
                case .failed(let error):
                    if !resumed.withLock({ let old = $0; $0 = true; return old }) {
                        continuation.resume(throwing: error)
                    }
                default:
                    break
                }
            }
            listener.start(queue: queue)
        }
    }

    func stop() {
        queue.sync {
            listener?.cancel()
            connections.forEach { $0.cancel() }
            connections = []
        }
    }

    var baseURL: URL { URL(string: "http://127.0.0.1:\(port)")! }

    // MARK: Test controls

    func state() -> Snapshot { queue.sync { snapshot } }

    /// Abruptly closes every open connection (no WebSocket close handshake).
    func dropConnections() {
        queue.sync {
            let open = connections
            connections = []
            open.forEach { $0.cancel() }
        }
    }

    /// While held, frames are stored but not acked.
    func setHoldAcks(_ hold: Bool) {
        queue.sync {
            holdAcks = hold
            if !hold { sendAcks() }
        }
    }

    func push(_ event: ServerEvent) {
        queue.sync { connections.forEach { send(event, on: $0) } }
    }

    // MARK: Connection handling

    private func accept(_ connection: NWConnection) {
        connections.append(connection)
        snapshot.connectionCount += 1
        connection.start(queue: queue)
        receive(on: connection)
    }

    private func receive(on connection: NWConnection) {
        connection.receiveMessage { [weak self] data, context, _, error in
            guard let self, error == nil else { return }
            let metadata = context?.protocolMetadata(definition: NWProtocolWebSocket.definition)
                as? NWProtocolWebSocket.Metadata
            if let data, let metadata {
                switch metadata.opcode {
                case .text: handleText(data, on: connection)
                case .binary: handleBinary(data)
                case .close: return
                default: break
                }
            }
            receive(on: connection)
        }
    }

    private func handleText(_ data: Data, on connection: NWConnection) {
        guard let message = try? JSONDecoder().decode(ClientMessage.self, from: data) else { return }
        switch message {
        case .hello(let hello):
            snapshot.hellos.append(hello)
            send(.helloAck(HelloAck(session: session, acked: snapshot.acked)), on: connection)
        case .marker(let marker):
            snapshot.markers.append(marker)
        case .draftSegment(let draft):
            snapshot.drafts.append(draft)
        case .end:
            snapshot.endReceived = true
            if let note = finalNoteOnEnd { send(.finalNote(note), on: connection) }
            if closeAfterEnd {
                // Let the final_note flush, then close like a real server would.
                connection.send(
                    content: nil,
                    contentContext: NWConnection.ContentContext(
                        identifier: "close",
                        metadata: [NWProtocolWebSocket.Metadata(opcode: .close)]
                    ),
                    isComplete: true,
                    completion: .contentProcessed { _ in }
                )
            }
        }
    }

    private func handleBinary(_ data: Data) {
        guard let frame = try? AudioFrame(decoding: data) else { return }
        snapshot.received[frame.channel, default: []].append(frame.seq)
        let current = snapshot.acked[frame.channel]
        if let current, frame.seq <= current {
            snapshot.duplicates += 1
        } else if frame.seq == (current.map { $0 + 1 } ?? 0) {
            snapshot.acked[frame.channel] = frame.seq
        } else {
            snapshot.gaps += 1
        }
        if !holdAcks { sendAcks() }
    }

    private func sendAcks() {
        for channel in Channel.allCases {
            guard let seq = snapshot.acked[channel] else { continue }
            connections.forEach { send(.ack(Ack(channel: channel, seq: seq)), on: $0) }
        }
    }

    private func send(_ event: ServerEvent, on connection: NWConnection) {
        guard let data = try? JSONEncoder().encode(event) else { return }
        connection.send(
            content: data,
            contentContext: NWConnection.ContentContext(
                identifier: "text",
                metadata: [NWProtocolWebSocket.Metadata(opcode: .text)]
            ),
            isComplete: true,
            completion: .contentProcessed { _ in }
        )
    }
}

/// Tiny lock-protected box for flags and counters touched from callbacks.
final class Mutex<Value: Sendable>: @unchecked Sendable {
    private var value: Value
    private let lock = NSLock()
    init(_ value: Value) { self.value = value }

    func withLock<R>(_ body: (inout Value) -> R) -> R {
        lock.lock()
        defer { lock.unlock() }
        return body(&value)
    }
}
