import AppKit
import JarvisLiveKit
import SwiftUI

/// What the filter row shows: the feed, or one kind of copilot output.
enum CopilotFilter: String, CaseIterable, Identifiable {
    case all = "All"
    case actions = "Actions"
    case decisions = "Decisions"
    case notes = "Notes"
    case related = "Related"

    var id: String { rawValue }
    var kind: CopilotKind? { CopilotKind(rawValue: rawValue) }
}

/// The Copilot panel, "B · Feed": a header with the recording state, a time-ordered feed of speech and
/// markers, and a "Now" dock with the newest suggestion. Three layouts by width (strip, standard,
/// two-column with a by-kind rail) and a collapsed state.
struct CopilotPanelView: View {
    @Bindable var model: AppModel
    @AppStorage("panelCollapsed") private var collapsed = false
    @State private var tracker = UnseenTracker()
    @State private var filter: CopilotFilter = .all

    var body: some View {
        GeometryReader { geo in
            let layout = PanelLayout.forWidth(geo.size.width)
            VStack(spacing: 0) {
                titleStrip
                if collapsed {
                    collapsedBody
                } else {
                    expandedBody(layout)
                }
            }
        }
        .background(Color.inst.bg)
        .foregroundStyle(Color.inst.text)
        .frame(minWidth: 300, minHeight: collapsed ? PanelController.collapsedHeight : 360)
        .onAppear { tracker.glance(ids: rows.map(\.id)) }
        // A glance is the pointer passing over the panel or a click on it; the panel never takes focus.
        .onHover { _ in tracker.glance(ids: rows.map(\.id)) }
        .simultaneousGesture(TapGesture().onEnded { tracker.glance(ids: rows.map(\.id)) })
    }

    // MARK: Data

    private var copilot: CopilotSnapshot? { model.store.copilot }

    private var rows: [FeedRow] {
        FeedBuilder.rows(lines: model.store.lines, markers: model.markers)
    }

    // MARK: Title strip

    private var titleStrip: some View {
        @Bindable var settings = model.settings
        return HStack(spacing: 8) {
            Text("Jarvis Live").instStyle(InstType.caption).fontWeight(.bold)
            if let channel = model.serverChannel { InstBadge(channel: channel) }
            Spacer()
            Button {
                collapsed.toggle()
                model.panel.setCollapsed(collapsed)
            } label: {
                Text(collapsed ? "\u{25BE}" : "\u{25B4}")
            }
            .buttonStyle(.plain)
            .foregroundStyle(Color.inst.text2)
            .accessibilityLabel(collapsed ? "Expand panel" : "Collapse panel")
            Toggle("On top", isOn: $settings.panelAlwaysOnTop)
                .toggleStyle(InstSwitchStyle())
                .onChange(of: model.settings.panelAlwaysOnTop) { model.panel.show() }
        }
        // The traffic lights sit in the left 70pt of the transparent title bar.
        .padding(.leading, 76).padding(.trailing, 8)
        .frame(height: 32)
        .background(Color.inst.surface)
        .overlay(alignment: .bottom) { Rectangle().fill(Color.inst.line).frame(height: 1) }
    }

    // MARK: Collapsed

    private var collapsedBody: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                liveState
                Spacer()
            }
            nowDock(compact: true)
        }
        .padding(10)
        .frame(maxHeight: .infinity, alignment: .top)
        .background(Color.inst.surface)
    }

    // MARK: Expanded

    private func expandedBody(_ layout: PanelLayout) -> some View {
        VStack(spacing: 0) {
            header(layout)
            if layout == .twoColumn {
                HStack(spacing: 0) {
                    VStack(spacing: 0) {
                        feed(layout)
                        nowDock(compact: false)
                    }
                    rail
                }
            } else {
                filterRow(layout)
                if filter == .all {
                    feed(layout)
                } else if let kind = filter.kind {
                    ScrollView { kindSection(kind).padding(8) }
                }
                nowDock(compact: layout == .strip)
            }
        }
    }

    // MARK: Header

    private func header(_ layout: PanelLayout) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 6) {
                liveState
                Spacer(minLength: 4)
                Button { model.addMarker() } label: {
                    Text(layout == .strip ? "\u{2691}" : "\u{2691} Add Marker")
                }
                .buttonStyle(InstButtonStyle())
                .disabled(!model.isRecording)
                .help(model.isRecording ? "" : "Start a session first.")
                .accessibilityLabel("Add marker")
                Button { Task { await model.stop() } } label: {
                    Text(layout == .strip ? "\u{25A0}" : "\u{25A0} Stop Session")
                }
                .buttonStyle(InstButtonStyle())
                .disabled(!model.isRecording)
                .help(model.isRecording ? "" : "Start a session first.")
                .accessibilityLabel("Stop session")
            }
            HStack(spacing: 8) {
                connectionChip
                if let stt = sttLine {
                    Text(stt).instStyle(InstType.meta).foregroundStyle(Color.inst.text2).lineLimit(1)
                }
            }
            if let note = connectionNote {
                InstHelp(note)
            }
            if !model.isRecording {
                InstHelp("Start a session first.")
            }
        }
        .padding(layout == .strip ? 8 : 12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.inst.surface)
    }

    /// REC pill with the clock while recording; a chip otherwise.
    @ViewBuilder private var liveState: some View {
        if model.isRecording {
            InstLivePill {
                InstDot(size: 8, breathing: true)
                Text("REC").instStyle(InstType.tag).foregroundStyle(Color.inst.accent)
                Text(ClockFormat.elapsed(seconds: model.elapsedSeconds))
                    .font(InstType.meta.font).fontDesign(.monospaced)
                InstMeter(height: 12)
            }
            .accessibilityElement(children: .ignore)
            .accessibilityLabel("Recording, \(ClockFormat.elapsed(seconds: model.elapsedSeconds))")
        } else if model.phase == .stopping {
            InstChip(text: "Finalizing", tone: .progress, spinner: true)
        } else if model.store.finalNote != nil {
            InstChip(text: "\u{2713} Done", tone: .done)
        } else {
            InstChip(text: "Idle")
        }
    }

    @ViewBuilder private var connectionChip: some View {
        switch model.link {
        case .connected: InstChip(text: "\u{25CF} Live", tone: .live)
        case .reconnecting: InstChip(text: "Reconnecting", tone: .progress, spinner: true)
        case .offlineLocal: InstChip(text: "Local-only")
        case .failed: InstChip(text: "\u{2715} Degraded", tone: .error)
        case .idle: EmptyView()
        }
    }

    private var connectionNote: String? {
        switch model.link {
        case .reconnecting: "Still recording. Nothing is lost."
        case .failed(let why): why
        default: nil
        }
    }

    private var sttLine: String? {
        switch model.link {
        case .connected:
            guard let tier = model.store.sttTier else { return nil }
            return "STT: server \(tier) \u{00B7} lag \(model.store.lagMs) ms"
        case .offlineLocal: return "STT: on-device draft"
        default: return nil
        }
    }

    // MARK: Filter

    private func filterRow(_ layout: PanelLayout) -> some View {
        Group {
            if layout == .strip {
                Picker("Show", selection: $filter) {
                    ForEach(CopilotFilter.allCases) { Text(title(for: $0)).tag($0) }
                }
                .pickerStyle(.menu).labelsHidden()
                .frame(maxWidth: .infinity, alignment: .leading)
            } else {
                InstSegmented(
                    options: CopilotFilter.allCases, selection: $filter,
                    title: { $0.rawValue }, badge: { badgeCount($0) }
                )
            }
        }
        .padding(.horizontal, 8).padding(.top, 8)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func badgeCount(_ option: CopilotFilter) -> Int? {
        guard let n = option.kind?.count(in: copilot), n > 0 else { return nil }
        return n
    }

    private func title(for option: CopilotFilter) -> String {
        guard let n = badgeCount(option) else { return option.rawValue }
        return "\(option.rawValue) \(n)"
    }

    // MARK: Feed

    private func feed(_ layout: PanelLayout) -> some View {
        let rows = self.rows
        let ids = rows.map(\.id)
        let separator = tracker.separatorIndex(in: ids)
        let unseen = Set(tracker.unseen(in: ids))
        return ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 4) {
                    if rows.isEmpty {
                        Text("\u{2014}").instStyle(InstType.body).foregroundStyle(Color.inst.text2).padding(8)
                    }
                    ForEach(Array(rows.enumerated()), id: \.element.id) { index, row in
                        if index == separator {
                            newSeparator(count: unseen.count)
                        }
                        feedRow(row, layout: layout, isNew: unseen.contains(row.id))
                    }
                    if let final = model.store.finalNote {
                        finalNoteRow(final.title)
                    }
                }
                .padding(.horizontal, 8).padding(.top, 8).padding(.bottom, 4)
            }
            .defaultScrollAnchor(.bottom)
            .onChange(of: ids.last) {
                if let last = ids.last { proxy.scrollTo(last, anchor: .bottom) }
            }
        }
        // Drafts become finals in place: no animation, so no line moves when the text is replaced.
        .transaction { $0.animation = nil }
        .frame(maxHeight: .infinity)
    }

    @ViewBuilder private func feedRow(_ row: FeedRow, layout: PanelLayout, isNew: Bool) -> some View {
        switch row {
        case .speech(let line): speechRow(line, layout: layout, isNew: isNew)
        case .marker(_, let tMs, let label):
            HStack(spacing: 4) {
                Text("\u{2691} \(label)")
                Text("\u{00B7} \(ClockFormat.stamp(ms: tMs))").font(InstType.meta.font).fontDesign(.monospaced)
            }
            .instStyle(InstType.meta).foregroundStyle(Color.inst.warning)
            .padding(.horizontal, 13).padding(.vertical, 4)
        }
    }

    private func speechRow(_ line: TranscriptLine, layout: PanelLayout, isNew: Bool) -> some View {
        let who = model.store.who(for: line)
        let me = who.kind == .me
        // Local-only drafts are never replaced, so they read as normal text.
        let ghost = line.isDraft && !model.sessionIsLocalOnly
        // An unnamed voice is muted and italic, a named one coloured: the words differ as well.
        let unknown = who.kind == .unknown
        let tag = Text(who.text)
            .instStyle(InstType.tag).italic(unknown).lineLimit(1)
            .foregroundStyle(me ? Color.inst.accent : unknown ? Color.inst.text2 : Color.inst.themText)
        // Names are longer than Me / Them, so the column widens once voices have been told apart.
        let tagWidth: CGFloat = model.store.speakers.isEmpty ? 32 : 72
        let time = Text(ClockFormat.stamp(ms: line.startMs))
            .font(InstType.meta.font).fontDesign(.monospaced).foregroundStyle(Color.inst.text2)
        let newMark = Text("NEW").instStyle(InstType.tag).foregroundStyle(Color.inst.accent)
        let text = Text(line.text)
            .instStyle(InstType.body)
            .italic(ghost)
            .foregroundStyle(ghost ? Color.inst.text2 : Color.inst.text)
        return HStack(alignment: .top, spacing: 8) {
            Rectangle().fill(me ? Color.inst.accent : unknown ? Color.inst.text2 : Color.inst.them).frame(width: 3)
            if layout == .strip {
                VStack(alignment: .leading, spacing: 2) {
                    HStack(spacing: 6) { tag; time; if isNew { newMark } }
                    text
                }
            } else {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    time.frame(width: 40, alignment: .leading)
                    tag.frame(width: tagWidth, alignment: .leading)
                    text
                    if isNew { newMark }
                }
            }
            Spacer(minLength: 0)
        }
        .padding(.vertical, 5).padding(.trailing, 8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(isNew ? Color.inst.accentTint : Color.inst.surface)
        .help(line.mixed ? "This line includes a change of speaker" : "")
        .accessibilityElement(children: .combine)
    }

    private func newSeparator(count: Int) -> some View {
        let since = tracker.glancedAt.map { $0.formatted(date: .omitted, time: .shortened) } ?? ""
        return HStack(spacing: 8) {
            Rectangle().fill(Color.inst.accent).frame(height: 1)
            Text("\(count) new since you looked at \(since)")
                .instStyle(InstType.meta).foregroundStyle(Color.inst.accent).lineLimit(1).fixedSize()
            Rectangle().fill(Color.inst.accent).frame(height: 1)
        }
        .padding(.vertical, 4)
        .accessibilityElement(children: .combine)
    }

    private func finalNoteRow(_ title: String) -> some View {
        HStack(spacing: 8) {
            InstSectionLabel(title: "Final note")
            Text(title).instStyle(InstType.body).foregroundStyle(Color.inst.accent)
            Spacer(minLength: 0)
        }
        .padding(8)
        .background(Color.inst.surface)
    }

    // MARK: Rail and kinds

    private var rail: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 4) {
                InstSectionLabel(title: "By kind")
                ForEach(CopilotKind.allCases) { kind in
                    if kind.count(in: copilot) > 0 { kindSection(kind) }
                }
                if CopilotKind.allCases.allSatisfy({ $0.count(in: copilot) == 0 }) {
                    Text("\u{2014}").instStyle(InstType.caption).foregroundStyle(Color.inst.text2)
                }
            }
            .padding(.horizontal, 12).padding(.vertical, 10)
        }
        .frame(width: 240)
        .overlay(alignment: .leading) { Rectangle().fill(Color.inst.line).frame(width: 1) }
    }

    private func kindSection(_ kind: CopilotKind) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Text(kind.rawValue).instStyle(InstType.caption).fontWeight(.bold)
                Text("\(kind.count(in: copilot))").font(InstType.meta.font).fontDesign(.monospaced)
                    .foregroundStyle(Color.inst.text2)
            }
            .padding(.top, 8)
            switch kind {
            case .notes:
                ForEach(copilot?.notes ?? []) { kindRow(Text($0.text)) }
            case .actions:
                ForEach(copilot?.actions ?? []) { action in
                    let meta = [action.owner, action.due].compactMap { $0 }.joined(separator: " \u{00B7} ")
                    kindRow(VStack(alignment: .leading, spacing: 2) {
                        Text("\u{25B8} \(action.text)")
                        if !meta.isEmpty { Text(meta).instStyle(InstType.meta).foregroundStyle(Color.inst.text2) }
                    })
                }
            case .decisions:
                ForEach(copilot?.decisions ?? []) { kindRow(Text($0.text)) }
            case .related:
                ForEach(copilot?.related ?? []) { relatedRow($0) }
            }
        }
    }

    private func kindRow<Content: View>(_ content: Content) -> some View {
        content
            .instStyle(InstType.caption)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 8).padding(.vertical, 5)
            .background(Color.inst.surface)
    }

    private func relatedRow(_ note: RelatedNote) -> some View {
        Button {
            if let url = RelatedLink.url(note.uri) { NSWorkspace.shared.open(url) }
        } label: {
            kindRow(VStack(alignment: .leading, spacing: 2) {
                Text(note.title).foregroundStyle(Color.inst.accent).lineLimit(1)
                Text(note.snippet).instStyle(InstType.meta).foregroundStyle(Color.inst.text2).lineLimit(2)
            })
        }
        .buttonStyle(.plain)
    }

    // MARK: Now dock

    private func nowDock(compact: Bool) -> some View {
        TimelineView(.periodic(from: .now, by: 1)) { _ in
            let nowMs = model.sessionClock?.elapsedMs() ?? 0
            let live = SuggestionAgeing.live(copilot?.suggestions ?? [], dismissed: model.dismissedSuggestionIDs, nowMs: nowMs)
            let split = SuggestionAgeing.split(live)
            VStack(alignment: .leading, spacing: 6) {
                HStack(spacing: 8) {
                    InstSectionLabel(title: "Now", color: Color.inst.text)
                    if let now = split.now {
                        Text(label(for: now.kind)).instStyle(InstType.tag).foregroundStyle(Color.inst.accent)
                        Spacer()
                        Text(ClockFormat.elapsed(seconds: max(0, (now.expiresAtMs - nowMs) / 1000)))
                            .font(InstType.meta.font).fontDesign(.monospaced).foregroundStyle(Color.inst.text2)
                        dismissButton(now)
                    } else {
                        Spacer()
                    }
                }
                if let now = split.now {
                    Text(now.text).instStyle(InstType.body).lineLimit(compact ? 2 : 3)
                        .opacity(SuggestionAgeing.opacity(remainingMs: now.expiresAtMs - nowMs))
                    waiting(split.waiting, nowMs: nowMs, compact: compact)
                } else {
                    Text(quietMessage).instStyle(InstType.caption).foregroundStyle(Color.inst.text2)
                }
            }
            .padding(10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.inst.surface)
            .overlay(alignment: .top) { Rectangle().fill(Color.inst.line).frame(height: 1) }
            .animation(.easeOut(duration: 0.6), value: split.now?.id)
        }
    }

    @ViewBuilder private func waiting(_ others: [CopilotSuggestion], nowMs: Int, compact: Bool) -> some View {
        if !others.isEmpty {
            if compact {
                let kinds = others.map { label(for: $0.kind).capitalized }.joined(separator: ", ")
                Text("\(others.count) more waiting \u{00B7} \(kinds)")
                    .instStyle(InstType.meta).foregroundStyle(Color.inst.text2)
            } else {
                ForEach(others.prefix(2)) { other in
                    HStack(spacing: 8) {
                        Text(label(for: other.kind)).instStyle(InstType.tag).foregroundStyle(Color.inst.text2)
                        Text(other.text).instStyle(InstType.caption).foregroundStyle(Color.inst.text2).lineLimit(1)
                        Spacer(minLength: 0)
                        Text(ClockFormat.elapsed(seconds: max(0, (other.expiresAtMs - nowMs) / 1000)))
                            .font(InstType.meta.font).fontDesign(.monospaced).foregroundStyle(Color.inst.text2)
                        dismissButton(other)
                    }
                    .opacity(SuggestionAgeing.opacity(remainingMs: other.expiresAtMs - nowMs))
                }
            }
        }
    }

    private func dismissButton(_ suggestion: CopilotSuggestion) -> some View {
        Button("\u{2715}") { model.dismissedSuggestionIDs.insert(suggestion.id) }
            .buttonStyle(.plain).foregroundStyle(Color.inst.text2)
            .accessibilityLabel("Dismiss suggestion")
    }

    private var quietMessage: String {
        if model.sessionIsLocalOnly && model.phase != .idle {
            return "Copilot is off in local-only mode. The server writes it after you upload."
        }
        if model.phase == .stopping { return "Writing the final note. It lands at the bottom of the feed." }
        if model.isRecording { return "Nothing to suggest yet." }
        return "Start a session to see suggestions."
    }

    private func label(for kind: SuggestionKind) -> String {
        switch kind {
        case .question: "QUESTION"
        case .gap: "GAP"
        case .counterpoint: "COUNTERPOINT"
        case .factCheck: "FACT CHECK"
        }
    }
}
