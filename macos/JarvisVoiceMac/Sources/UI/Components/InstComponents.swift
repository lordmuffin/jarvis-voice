import JarvisLiveKit
import SwiftUI

// Instrument components for SwiftUI, built only from the vendored tokens (Color.inst, InstType,
// InstSpace, InstRadius, InstSize, InstMotion). They follow the system appearance.

/// The server's channel as a filled pill: LIVE and LAB each have their own colour, anything else is grey.
struct InstBadge: View {
    let channel: ServerChannel

    var body: some View {
        Text(channel.tag)
            .instStyle(InstType.tag)
            .padding(.horizontal, 6)
            .frame(height: InstSize.chip)
            .foregroundStyle(Color.inst.onChannel)
            .background(fill, in: Capsule())
            .accessibilityLabel("\(channel.tag) server")
    }

    private var fill: Color {
        switch channel {
        case .live: Color.inst.channelLive
        case .lab: Color.inst.channelLab
        case .other: Color.inst.channelOther
        }
    }
}

/// A small status chip: coloured text on `surface2`, always with a word or glyph.
struct InstChip: View {
    enum Tone { case neutral, live, progress, done, error }

    let text: String
    var tone: Tone = .neutral
    var spinner = false

    var body: some View {
        HStack(spacing: 4) {
            if spinner { InstSpinner().frame(width: 10, height: 10) }
            Text(text).instStyle(InstType.meta).lineLimit(1)
        }
        .padding(.horizontal, 6)
        .frame(height: InstSize.chip)
        .foregroundStyle(color)
        .background(Color.inst.surface2, in: RoundedRectangle(cornerRadius: InstRadius.r0))
    }

    private var color: Color {
        switch tone {
        case .neutral: Color.inst.text2
        case .live: Color.inst.accent
        case .progress: Color.inst.warning
        case .done: Color.inst.success
        case .error: Color.inst.error
        }
    }
}

/// The idle dot's heartbeat: scales 1 → 1.4 and fades while the session is live. Still under Reduce Motion.
struct InstDot: View {
    var color: Color = Color.inst.accent
    var size: CGFloat = InstSize.dot
    var breathing = false
    var hollow = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var out = false

    var body: some View {
        Circle()
            .fill(hollow ? Color.clear : color)
            .overlay(Circle().stroke(color, lineWidth: hollow ? 1.5 : 0))
            .frame(width: size, height: size)
            .scaleEffect(breathing && !reduceMotion && out ? 1.4 : 1)
            .opacity(breathing && !reduceMotion && !out ? 0.85 : 1)
            .onAppear {
                guard breathing, !reduceMotion else { return }
                withAnimation(.easeInOut(duration: InstMotion.breathe).repeatForever(autoreverses: true)) { out = true }
            }
            .accessibilityHidden(true)
    }
}

/// Three recording bars, each pulsing 20% → 100% on its own period.
struct InstMeter: View {
    var color: Color = Color.inst.accent
    var height: CGFloat = 14
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        TimelineView(.animation(minimumInterval: 1 / 30, paused: reduceMotion)) { context in
            let t = context.date.timeIntervalSinceReferenceDate
            HStack(alignment: .center, spacing: 2) {
                ForEach(0..<3, id: \.self) { i in
                    Rectangle()
                        .fill(color)
                        .frame(width: 3, height: height * level(at: t, bar: i))
                }
            }
            .frame(height: height)
        }
        .accessibilityHidden(true)
    }

    private func level(at t: TimeInterval, bar i: Int) -> CGFloat {
        if reduceMotion { return 0.6 }
        let period = 0.6 + 0.12 * Double(i)
        let phase = ((t - 0.08 * Double(i)) / period).truncatingRemainder(dividingBy: 2)
        let tri = phase < 1 ? phase : 2 - phase  // 0 → 1 → 0, linear
        return 0.2 + 0.8 * CGFloat(tri)
    }
}

/// The processing spinner: an indeterminate ring.
struct InstSpinner: View {
    var color: Color = Color.inst.text2
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var turn = false

    var body: some View {
        Circle()
            .trim(from: 0, to: 0.75)
            .stroke(color, style: StrokeStyle(lineWidth: 1.5, lineCap: .square))
            .rotationEffect(.degrees(turn ? 360 : 0))
            .onAppear {
                guard !reduceMotion else { return }
                withAnimation(.linear(duration: InstMotion.spinner).repeatForever(autoreverses: false)) { turn = true }
            }
            .accessibilityHidden(true)
    }
}

/// The Rec / Listening pill: a hairline-bordered capsule that holds a dot, a label and the meter.
struct InstLivePill<Content: View>: View {
    var border: Color = Color.inst.accent
    @ViewBuilder var content: Content

    var body: some View {
        HStack(spacing: 8) { content }
            .padding(.horizontal, 10)
            .frame(minHeight: InstSize.livePillInline)
            .background(Color.inst.surface, in: Capsule())
            .overlay(Capsule().stroke(border, lineWidth: 1))
    }
}

/// A section heading: uppercase, tracked, secondary text.
struct InstSectionLabel: View {
    let title: String
    var color: Color = Color.inst.text2

    var body: some View {
        Text(title).instStyle(InstType.label).foregroundStyle(color).accessibilityAddTraits(.isHeader)
    }
}

/// A grouped block on `surface` with the default inner padding, used by Settings.
struct InstGroup<Content: View>: View {
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 14) { content }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(InstSpace.s12)
            .background(Color.inst.surface)
    }
}

/// Help text under a control.
struct InstHelp: View {
    let text: String
    var warning = false

    init(_ text: String, warning: Bool = false) {
        self.text = text
        self.warning = warning
    }

    var body: some View {
        Text(text)
            .instStyle(InstType.meta)
            .foregroundStyle(warning ? Color.inst.warning : Color.inst.text2)
            .fixedSize(horizontal: false, vertical: true)
    }
}

/// A 36×20 switch with the state word beside the label.
struct InstSwitchStyle: ToggleStyle {
    func makeBody(configuration: Configuration) -> some View {
        Button { configuration.isOn.toggle() } label: {
            HStack(spacing: 8) {
                ZStack(alignment: configuration.isOn ? .trailing : .leading) {
                    Capsule().fill(configuration.isOn ? Color.inst.accent : Color.inst.control)
                    Circle()
                        .fill(configuration.isOn ? Color.inst.onAccent : Color.inst.surface)
                        .frame(width: InstSize.switchKnob, height: InstSize.switchKnob)
                        .padding(3)
                }
                .frame(width: InstSize.switchWidth, height: InstSize.switchHeight)
                Text(configuration.isOn ? "ON" : "OFF")
                    .instStyle(InstType.tag)
                    .foregroundStyle(configuration.isOn ? Color.inst.accent : Color.inst.text2)
                configuration.label.instStyle(InstType.body).foregroundStyle(Color.inst.text)
            }
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(.isToggle)
    }
}

/// A segmented control: a `surface2` track with the chosen segment raised to `accent`.
struct InstSegmented<Value: Hashable & Identifiable>: View {
    let options: [Value]
    @Binding var selection: Value
    let title: (Value) -> String
    var badge: (Value) -> Int? = { _ in nil }

    var body: some View {
        HStack(spacing: 2) {
            ForEach(options) { option in
                let on = option == selection
                Button { selection = option } label: {
                    HStack(spacing: 4) {
                        Text(title(option))
                        if let n = badge(option) { Text("\(n)").font(InstType.meta.font) }
                    }
                    .instStyle(InstType.caption)
                    .padding(.horizontal, 8)
                    .frame(height: InstSize.controlSm - 6)
                    .foregroundStyle(on ? Color.inst.onAccent : Color.inst.text)
                    .background(on ? Color.inst.accent : Color.clear)
                }
                .buttonStyle(.plain)
                .accessibilityAddTraits(on ? .isSelected : [])
            }
        }
        .padding(2)
        .background(Color.inst.surface2)
    }
}

/// A secondary icon/text button: `surface2` fill, `control` outline.
struct InstButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .instStyle(InstType.caption)
            .foregroundStyle(Color.inst.text)
            .padding(.horizontal, 10)
            .frame(minHeight: InstSize.controlSm - 6)
            .background(Color.inst.surface2.opacity(configuration.isPressed ? 0.7 : 1))
            .overlay(Rectangle().stroke(Color.inst.control, lineWidth: 1))
    }
}
