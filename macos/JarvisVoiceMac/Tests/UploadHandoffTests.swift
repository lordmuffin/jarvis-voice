import XCTest

@testable import Jarvis_Voice

final class UploadHandoffTests: XCTestCase {
    private var root: URL!

    override func setUpWithError() throws {
        root = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString, directoryHint: .isDirectory)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: root)
    }

    private func makeSession(_ id: String) throws {
        let dir = root.appending(path: id, directoryHint: .isDirectory)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        try Data([1, 2, 3]).write(to: dir.appending(path: "mic.pcm"))
    }

    private func exists(_ id: String) -> Bool {
        FileManager.default.fileExists(atPath: root.appending(path: id).path)
    }

    func testMovesTheLocalRecordingUnderTheServerId() throws {
        try makeSession("local")
        XCTAssertEqual(try UploadHandoff.relocate(root: root, from: "local", to: "srv"), .moved)
        XCTAssertFalse(exists("local"))
        XCTAssertTrue(exists("srv"))
    }

    /// An interrupted upload is retried under the same server session: the audio is already there.
    func testRetryFindsTheAudioAlreadyUnderTheServerId() throws {
        try makeSession("srv")
        XCTAssertEqual(try UploadHandoff.relocate(root: root, from: "local", to: "srv"), .alreadyThere)
        XCTAssertTrue(exists("srv"))
    }

    func testMissingAudioIsAnError() {
        XCTAssertThrowsError(try UploadHandoff.relocate(root: root, from: "local", to: "srv")) {
            XCTAssertEqual($0 as? AppError, .audioMissing)
        }
    }
}
