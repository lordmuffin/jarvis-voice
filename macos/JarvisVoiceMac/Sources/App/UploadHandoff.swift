import Foundation

/// Gets a recording's audio under the server's session id, once, however many times an upload is tried.
enum UploadHandoff {
    enum Outcome: Equatable { case moved, alreadyThere }

    /// The outbox is keyed by session id. The first attempt moves the local recording under the server's
    /// id; a retry after an interruption finds it already there.
    @discardableResult
    static func relocate(root: URL, from localId: String, to serverId: String) throws -> Outcome {
        let fileManager = FileManager.default
        let source = root.appending(path: localId, directoryHint: .isDirectory)
        let destination = root.appending(path: serverId, directoryHint: .isDirectory)
        if fileManager.fileExists(atPath: destination.path) { return .alreadyThere }
        guard fileManager.fileExists(atPath: source.path) else { throw AppError.audioMissing }
        try fileManager.moveItem(at: source, to: destination)
        return .moved
    }
}
