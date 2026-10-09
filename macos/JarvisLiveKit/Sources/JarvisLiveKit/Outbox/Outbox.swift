import Foundation

public enum OutboxError: Error, Equatable, Sendable {
    case sessionNotFound(String)
    /// Frames must be appended with contiguous seq numbers per channel.
    case unexpectedSeq(channel: Channel, expected: UInt32, got: UInt32)
    case invalidPayloadSize(Int)
    case corrupt(String)
}

/// Durable, append-only store of audio frames for one session.
///
/// Layout under `<root>/<sessionId>/`:
///
/// - `<channel>.pcm`         — concatenated frame payloads
/// - `<channel>.index.jsonl` — one `{"seq","t_ms","offset","len"}` object per line, appended after
///                             the payload is written (so a torn write leaves at worst an unindexed
///                             tail in the pcm file, which is truncated on reopen)
/// - `acked.json`            — `{"mic": n|null, "system": n|null}`, the cumulative server ack
///
/// The outbox is the single source of what has been captured; only `markAcked` (driven by the
/// server's `ack`) frees data. Writes are visible to other processes immediately but are not
/// `fsync`ed per frame, so the guarantee is survival of an app crash, not of power loss.
public actor Outbox {
    /// `~/Library/Application Support/JarvisVoice/sessions`
    public static var defaultRoot: URL {
        URL.applicationSupportDirectory
            .appending(path: "JarvisVoice", directoryHint: .isDirectory)
            .appending(path: "sessions", directoryHint: .isDirectory)
    }

    /// Once a channel is fully acked and its pcm file exceeds this size, the files are truncated.
    static let compactionThresholdBytes: UInt64 = 4 * 1024 * 1024

    public nonisolated let sessionId: String
    public nonisolated let directory: URL

    private struct IndexEntry: Codable, Equatable {
        var seq: UInt32
        var tMs: UInt32
        var offset: UInt64
        var len: Int

        enum CodingKeys: String, CodingKey {
            case seq, offset, len
            case tMs = "t_ms"
        }
    }

    private struct ChannelState {
        var entries: [IndexEntry] = []
        var pcm: FileHandle
        var index: FileHandle
        var pcmEnd: UInt64
    }

    private var channels: [Channel: ChannelState] = [:]
    private var acked = ChannelSeqs()

    /// Creates the session directory if needed, or opens and recovers an existing one.
    public init(sessionId: String, root: URL = Outbox.defaultRoot) throws {
        let dir = root.appending(path: sessionId, directoryHint: .isDirectory)
        try self.init(sessionId: sessionId, directory: dir, create: true)
    }

    /// Opens an existing session after a crash or relaunch. Throws if it was never created.
    public static func reopen(sessionId: String, root: URL = Outbox.defaultRoot) throws -> Outbox {
        let dir = root.appending(path: sessionId, directoryHint: .isDirectory)
        guard FileManager.default.fileExists(atPath: dir.path) else {
            throw OutboxError.sessionNotFound(sessionId)
        }
        return try Outbox(sessionId: sessionId, directory: dir, create: false)
    }

    private init(sessionId: String, directory: URL, create: Bool) throws {
        self.sessionId = sessionId
        self.directory = directory
        let fm = FileManager.default
        if create { try fm.createDirectory(at: directory, withIntermediateDirectories: true) }

        let ackedURL = directory.appending(path: "acked.json")
        var loadedAcked = ChannelSeqs()
        if let data = try? Data(contentsOf: ackedURL) {
            loadedAcked = try JSONDecoder().decode(ChannelSeqs.self, from: data)
        }
        acked = loadedAcked

        for channel in Channel.allCases {
            channels[channel] = try Self.openChannel(channel, in: directory)
        }
    }

    // MARK: - Public API

    /// The seq the next appended frame on `channel` must carry.
    public func nextSeq(for channel: Channel) -> UInt32 {
        let fromEntries = channels[channel]?.entries.last.map { $0.seq &+ 1 }
        let fromAck = acked[channel].map { $0 &+ 1 }
        return max(fromEntries ?? 0, fromAck ?? 0)
    }

    /// Cumulative server ack for `channel`, or `nil` if none yet.
    public func ackedSeq(for channel: Channel) -> UInt32? { acked[channel] }

    /// Snapshot of the cumulative acks, in the shape `hello.resume` expects.
    public func ackedSeqs() -> ChannelSeqs { acked }

    /// Appends a frame. Its `seq` must equal `nextSeq(for:)`.
    public func append(_ frame: AudioFrame) throws {
        guard AudioFrame.isValidPayloadSize(frame.payload.count) else {
            throw OutboxError.invalidPayloadSize(frame.payload.count)
        }
        let expected = nextSeq(for: frame.channel)
        guard frame.seq == expected else {
            throw OutboxError.unexpectedSeq(channel: frame.channel, expected: expected, got: frame.seq)
        }
        var state = channels[frame.channel]!
        let entry = IndexEntry(seq: frame.seq, tMs: frame.tMs, offset: state.pcmEnd, len: frame.payload.count)

        try state.pcm.seek(toOffset: state.pcmEnd)
        try state.pcm.write(contentsOf: frame.payload)
        // Index after payload: an index line never points at bytes that were not written.
        var line = try JSONEncoder().encode(entry)
        line.append(0x0A)
        try state.index.seekToEnd()
        try state.index.write(contentsOf: line)

        state.pcmEnd += UInt64(frame.payload.count)
        state.entries.append(entry)
        channels[frame.channel] = state
    }

    /// Convenience: assigns the next seq and appends. Returns the stored frame.
    @discardableResult
    public func append(channel: Channel, payload: Data, tMs: UInt32) throws -> AudioFrame {
        let frame = AudioFrame(channel: channel, seq: nextSeq(for: channel), tMs: tMs, payload: payload)
        try append(frame)
        return frame
    }

    /// Frames on `channel` that the server has not acked, in seq order.
    ///
    /// - Parameters:
    ///   - from: only return frames with `seq >= from` (e.g. the next seq not yet sent on the
    ///     current connection). `nil` returns everything unacked.
    ///   - limit: cap on the number of frames returned.
    public func unacked(_ channel: Channel, from: UInt32? = nil, limit: Int? = nil) throws -> [AudioFrame] {
        guard let state = channels[channel] else { return [] }
        let floor = max(from ?? 0, acked[channel].map { $0 &+ 1 } ?? 0)
        var result: [AudioFrame] = []
        for entry in state.entries where entry.seq >= floor {
            if let limit, result.count >= limit { break }
            try state.pcm.seek(toOffset: entry.offset)
            guard let payload = try state.pcm.read(upToCount: entry.len), payload.count == entry.len else {
                throw OutboxError.corrupt("short read at seq \(entry.seq) on \(channel.rawValue)")
            }
            result.append(AudioFrame(channel: channel, seq: entry.seq, tMs: entry.tMs, payload: payload))
        }
        return result
    }

    /// Audio duration (ms) of frames in `(acked, through]`; `through == nil` means "all stored".
    public func unackedDurationMs(_ channel: Channel, through: UInt32? = nil) -> Int {
        guard let state = channels[channel] else { return 0 }
        let floor = acked[channel].map { $0 &+ 1 } ?? 0
        var bytes = 0
        for entry in state.entries where entry.seq >= floor {
            if let through, entry.seq > through { break }
            bytes += entry.len
        }
        return bytes / AudioFrame.bytesPerMs
    }

    /// Records the server's cumulative ack. Lower or equal values are ignored.
    public func markAcked(_ channel: Channel, seq: UInt32) throws {
        if let current = acked[channel], seq <= current { return }
        acked[channel] = seq
        try persistAcked()
        try compactIfFullyAcked(channel)
    }

    /// Releases file handles. The instance must not be used afterwards.
    public func close() {
        for state in channels.values {
            try? state.pcm.close()
            try? state.index.close()
        }
        channels = [:]
    }

    // MARK: - Persistence

    private func persistAcked() throws {
        let data = try JSONEncoder().encode(acked)
        try data.write(to: directory.appending(path: "acked.json"), options: .atomic)
    }

    private func compactIfFullyAcked(_ channel: Channel) throws {
        guard var state = channels[channel],
              let last = state.entries.last?.seq,
              let ack = acked[channel], ack >= last,
              state.pcmEnd >= Self.compactionThresholdBytes
        else { return }
        try state.pcm.truncate(atOffset: 0)
        try state.index.truncate(atOffset: 0)
        state.entries = []
        state.pcmEnd = 0
        channels[channel] = state
    }

    private static func openChannel(_ channel: Channel, in directory: URL) throws -> ChannelState {
        let fm = FileManager.default
        let pcmURL = directory.appending(path: "\(channel.rawValue).pcm")
        let indexURL = directory.appending(path: "\(channel.rawValue).index.jsonl")
        for url in [pcmURL, indexURL] where !fm.fileExists(atPath: url.path) {
            guard fm.createFile(atPath: url.path, contents: nil) else {
                throw OutboxError.corrupt("cannot create \(url.lastPathComponent)")
            }
        }
        let pcmSize = (try fm.attributesOfItem(atPath: pcmURL.path)[.size] as? UInt64) ?? 0

        // Recover: keep the longest valid, contiguous prefix of the index whose data exists on disk.
        let raw = try Data(contentsOf: indexURL)
        let decoder = JSONDecoder()
        var entries: [IndexEntry] = []
        var validIndexBytes = 0
        var dataEnd: UInt64 = 0
        var cursor = raw.startIndex
        while let newline = raw[cursor...].firstIndex(of: 0x0A) {
            let lineData = raw[cursor..<newline]
            guard let entry = try? decoder.decode(IndexEntry.self, from: lineData),
                  entry.offset == dataEnd,
                  entry.offset + UInt64(entry.len) <= pcmSize,
                  entries.last.map({ $0.seq &+ 1 == entry.seq }) ?? true
            else { break }
            entries.append(entry)
            dataEnd = entry.offset + UInt64(entry.len)
            cursor = raw.index(after: newline)
            validIndexBytes = cursor - raw.startIndex
        }

        let pcm = try FileHandle(forUpdating: pcmURL)
        let index = try FileHandle(forUpdating: indexURL)
        try pcm.truncate(atOffset: dataEnd)
        try index.truncate(atOffset: UInt64(validIndexBytes))
        return ChannelState(entries: entries, pcm: pcm, index: index, pcmEnd: dataEnd)
    }
}
