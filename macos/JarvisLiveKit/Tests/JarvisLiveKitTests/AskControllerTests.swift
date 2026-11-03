import CoreAudio
import Foundation
import Testing
@testable import JarvisLiveKit

/// Hold to ask on the Mac (docs/plans/voice-push-to-talk.md): what is sent, said and shown.
private final class Rig: @unchecked Sendable {
    final class Speaker: AskSpeaker, @unchecked Sendable {
        var said: [String] = []
        var stopped = 0
        var firstAudio: (@Sendable () -> Void)?
        var done: (@Sendable () -> Void)?
        var failed: (@Sendable () -> Void)?

        func speak(
            _ text: String,
            onFirstAudio: @escaping @Sendable () -> Void,
            onDone: @escaping @Sendable () -> Void,
            onError: @escaping @Sendable () -> Void
        ) {
            said.append(text)
            firstAudio = onFirstAudio
            done = onDone
            failed = onError
        }

        func stop() { stopped += 1 }
    }

    var now: Int = 10_000
    var closed = true
    var sent: [ClientMessage] = []
    let speaker = Speaker()
    lazy var ask = AskController(
        send: { [unowned self] in self.sent.append($0) },
        speaker: speaker,
        routeClosed: { [unowned self] in self.closed },
        clock: { [unowned self] in self.now },
        streamMs: { 99 },
        newId: { "a1" }
    )

    func askAndRelease() {
        ask.onAvailable(true)
        ask.down(tMs: 60_000)
        now += 2_000
        ask.up(tMs: 62_000)
    }

    func reply(_ text: String = "Priya runs it.", outcome: AskOutcome = .answer, id: String = "a1") {
        ask.onReply(AskReply(id: id, outcome: outcome, text: text))
    }

    var lastPlayed: AskPlayed? {
        if case .askPlayed(let p)? = sent.last { return p }
        return nil
    }
}

@Suite("Hold to ask")
struct AskControllerTests {
    @Test func hiddenUntilTheServerOffersIt() {
        let rig = Rig()
        #expect(rig.ask.ui == .hidden)
        rig.ask.down(tMs: 1)
        #expect(rig.sent.isEmpty)
        rig.ask.onAvailable(true)
        #expect(rig.ask.ui == .ready)
    }

    @Test func aHoldSendsItsMarkersOnTheFramesClock() {
        let rig = Rig()
        rig.askAndRelease()
        #expect(rig.sent == [.askBegin(AskBegin(id: "a1", tMs: 60_000)), .askEnd(AskEnd(id: "a1", tMs: 62_000))])
        #expect(rig.ask.ui == .waiting)
    }

    @Test func theReplyIsSpokenAndTimedFromRelease() {
        let rig = Rig()
        rig.askAndRelease()
        rig.now += 1_000
        rig.reply()
        #expect(rig.speaker.said == ["Priya runs it."])
        rig.now += 2_400
        rig.speaker.firstAudio?()
        #expect(rig.lastPlayed == AskPlayed(id: "a1", state: .started, tMs: 99, latencyMs: 3_400))
        #expect(rig.ask.ui == .speaking("Priya runs it."))
        rig.speaker.done?()
        #expect(rig.lastPlayed?.state == .done)
        #expect(rig.ask.ui == .answered("Priya runs it.", spoken: true, why: nil))
    }

    @Test func noHeadphonesMeansTextOnly() {
        let rig = Rig()
        rig.askAndRelease()
        rig.closed = false
        rig.reply()
        #expect(rig.speaker.said.isEmpty)
        #expect(rig.lastPlayed?.state == .skipped && rig.lastPlayed?.reason == .route)
        #expect(rig.ask.ui == .answered("Priya runs it.", spoken: false, why: .route))
    }

    @Test func headphonesLostWhileSpeakingStopAtOnce() {
        let rig = Rig()
        rig.askAndRelease()
        rig.reply()
        rig.speaker.firstAudio?()
        rig.ask.onRouteChanged(closed: false)
        #expect(rig.speaker.stopped == 1)
        #expect(rig.lastPlayed?.reason == .interrupted)
    }

    @Test func aLateReplyIsShownNotSpoken() {
        let rig = Rig()
        rig.askAndRelease()
        rig.now += 31_000
        rig.reply()
        #expect(rig.speaker.said.isEmpty)
        #expect(rig.lastPlayed?.reason == .stale)
    }

    @Test func aReplyToAnOlderAskIsIgnored() {
        let rig = Rig()
        rig.askAndRelease()
        rig.reply(id: "old")
        #expect(rig.speaker.said.isEmpty)
        #expect(rig.ask.ui == .waiting)
    }

    @Test func aNewHoldStopsSpeech() {
        let rig = Rig()
        rig.askAndRelease()
        rig.reply()
        rig.speaker.firstAudio?()
        rig.ask.down(tMs: 70_000)
        #expect(rig.speaker.stopped == 1)
        #expect(rig.ask.ui == .listening)
    }

    @Test func nothingHeardSaysNothing() {
        let rig = Rig()
        rig.askAndRelease()
        rig.reply("", outcome: .empty)
        #expect(rig.speaker.said.isEmpty)
        #expect(rig.ask.ui == .answered("", spoken: false, why: .nothingHeard))
    }

    @Test func aSpeechFailureIsReported() {
        let rig = Rig()
        rig.askAndRelease()
        rig.reply()
        rig.speaker.failed?()
        #expect(rig.lastPlayed?.reason == .ttsFailed)
        #expect(rig.ask.ui == .answered("Priya runs it.", spoken: false, why: .speechFailed))
    }

    @Test func losingTheConnectionEndsEverything() {
        let rig = Rig()
        rig.askAndRelease()
        rig.reply()
        rig.ask.onAvailable(false)
        #expect(rig.speaker.stopped == 1)
        #expect(rig.ask.ui == .hidden)
        let before = rig.sent.count
        rig.ask.down(tMs: 80_000)
        #expect(rig.sent.count == before)
    }

    @Test func onlyClosedOutputsCount() {
        let headphones = UInt32(0x6864_706E)  // 'hdpn'
        let speaker = UInt32(0x6973_706B)  // 'ispk'
        #expect(OutputRoutes.isClosed(transport: kAudioDeviceTransportTypeBuiltIn, dataSource: headphones))
        #expect(!OutputRoutes.isClosed(transport: kAudioDeviceTransportTypeBuiltIn, dataSource: speaker))
        #expect(!OutputRoutes.isClosed(transport: kAudioDeviceTransportTypeBuiltIn, dataSource: nil))
        #expect(OutputRoutes.isClosed(transport: kAudioDeviceTransportTypeBluetooth, dataSource: nil))
        #expect(OutputRoutes.isClosed(transport: kAudioDeviceTransportTypeBluetoothLE, dataSource: nil))
        #expect(OutputRoutes.isClosed(transport: kAudioDeviceTransportTypeUSB, dataSource: nil))
        #expect(!OutputRoutes.isClosed(transport: kAudioDeviceTransportTypeHDMI, dataSource: nil))
        #expect(!OutputRoutes.isClosed(transport: kAudioDeviceTransportTypeDisplayPort, dataSource: nil))
        #expect(!OutputRoutes.isClosed(transport: kAudioDeviceTransportTypeAirPlay, dataSource: nil))
        #expect(!OutputRoutes.isClosed(transport: kAudioDeviceTransportTypeVirtual, dataSource: nil))
        #expect(!OutputRoutes.isClosed(transport: kAudioDeviceTransportTypeAggregate, dataSource: nil))
        #expect(!OutputRoutes.isClosed(transport: 0, dataSource: nil))
    }
}
