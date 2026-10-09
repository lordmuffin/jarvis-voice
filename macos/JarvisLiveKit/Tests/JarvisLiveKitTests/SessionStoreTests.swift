import Foundation
import Testing
@testable import JarvisLiveKit

@MainActor
@Suite("SessionStore")
struct SessionStoreTests {
    private func segment(
        _ id: String, _ start: Int, _ end: Int, channel: Channel = .mic, text: String = "t"
    ) -> ServerEvent {
        .segment(Segment(
            id: id, channel: channel, speaker: channel == .mic ? .me : .them,
            startMs: start, endMs: end, text: text, sttTier: "local"
        ))
    }

    private func draft(_ start: Int, _ end: Int, channel: Channel = .mic, text: String = "d") -> DraftSegment {
        DraftSegment(channel: channel, startMs: start, endMs: end, text: text, final: false)
    }

    @Test func finalSegmentReplacesOverlappingDraftOnSameChannel() {
        let store = SessionStore()
        store.applyDraft(draft(1000, 2400, text: "lets get start"))
        #expect(store.lines.count == 1)
        #expect(store.lines[0].isDraft)

        store.apply(segment("seg_1", 900, 2500, text: "Let's get started"))
        #expect(store.lines.count == 1)
        #expect(store.lines[0].id == "seg_1")
        #expect(store.lines[0].isDraft == false)
        #expect(store.lines[0].text == "Let's get started")
    }

    @Test func finalSegmentLeavesOtherChannelAndDisjointDraftsAlone() {
        let store = SessionStore()
        store.applyDraft(draft(1000, 2000, channel: .system, text: "them"))
        store.applyDraft(draft(5000, 6000, text: "later")) // disjoint, same channel
        store.apply(segment("a", 1000, 2000, channel: .mic))

        #expect(store.lines.map(\.text) == ["t", "them", "later"])
        #expect(store.lines.count == 3)
        #expect(store.lines.filter(\.isDraft).count == 2)
    }

    @Test func linesStayOrderedByStartTime() {
        let store = SessionStore()
        store.apply(segment("c", 3000, 4000))
        store.apply(segment("a", 0, 1000))
        store.applyDraft(draft(1500, 2500))
        store.apply(segment("b", 1000, 1500, channel: .system))
        #expect(store.lines.map(\.startMs) == [0, 1000, 1500, 3000])
    }

    @Test func newerDraftSupersedesEarlierDraftOfSameUtterance() {
        let store = SessionStore()
        store.applyDraft(draft(1000, 1500, text: "hel"))
        store.applyDraft(draft(1000, 2000, text: "hello wor"))
        store.applyDraft(draft(1000, 2400, text: "hello world"))
        #expect(store.lines.count == 1)
        #expect(store.lines[0].text == "hello world")
    }

    @Test func staleDraftAfterFinalIsIgnored() {
        let store = SessionStore()
        store.apply(segment("seg", 1000, 2000, text: "final"))
        store.applyDraft(draft(1000, 2000, text: "late draft"))
        #expect(store.lines.map(\.text) == ["final"])
    }

    @Test func redeliveredSegmentIsIdempotentAndUpdatesInPlace() {
        let store = SessionStore()
        store.apply(segment("s", 0, 1000, text: "one"))
        store.apply(segment("s", 0, 1000, text: "one"))
        store.apply(segment("s", 0, 1000, text: "one revised"))
        #expect(store.lines.count == 1)
        #expect(store.lines[0].text == "one revised")
    }

    @Test func copilotReplacesOnlyWhenVersionIsHigher() {
        let store = SessionStore()
        let v3 = CopilotSnapshot(version: 3, notes: [CopilotNote(id: "n", text: "three")])
        let v2 = CopilotSnapshot(version: 2, notes: [CopilotNote(id: "n", text: "two")])
        let v4 = CopilotSnapshot(version: 4, notes: [], decisions: [CopilotNote(id: "d", text: "four")])

        store.apply(.copilot(v3))
        store.apply(.copilot(v2))   // stale
        store.apply(.copilot(v3))   // duplicate
        #expect(store.copilot == v3)

        store.apply(.copilot(v4))   // full state: notes are gone, not merged
        #expect(store.copilot == v4)
        #expect(store.copilot?.notes.isEmpty == true)
    }

    @Test func firstSnapshotIsAcceptedEvenAtVersionZero() {
        let store = SessionStore()
        store.apply(.copilot(CopilotSnapshot(version: 0)))
        #expect(store.copilot?.version == 0)
    }

    @Test func statusUpdatesTierAndLag() {
        let store = SessionStore()
        store.apply(.status(Status(sttTier: "local", llmOk: true, lagMs: 180)))
        #expect(store.sttTier == "local")
        #expect(store.lagMs == 180)
        store.apply(.status(Status(sttTier: nil, llmOk: false, lagMs: 0)))
        #expect(store.sttTier == nil)
        #expect(store.llmOk == false)
    }

    @Test func connectionFinalNoteAndErrorAreRecorded() {
        let store = SessionStore()
        store.setConnection(.reconnecting(attempt: 2))
        #expect(store.connection == .reconnecting(attempt: 2))
        store.apply(.finalNote(FinalNote(path: "a.md", title: "A")))
        store.apply(.error(ServerError(code: "x", message: "boom")))
        #expect(store.finalNote?.title == "A")
        #expect(store.lastError?.code == "x")
    }
}
