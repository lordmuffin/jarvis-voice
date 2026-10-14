import AppKit

/// Draws the menu-bar item from a `MenuBarIconSpec`. Always a template image, so the menu bar tints it
/// for light, dark and wallpaper. State is shape: a waveform when idle, an outlined capsule with the
/// elapsed time while recording, a filled capsule with a microphone while dictating. The channel badge
/// beside it is a filled pill for LAB (letters cut out) and an outlined one for LIVE.
enum MenuBarIcon {
    private static let height: CGFloat = 16
    private static let pillHeight: CGFloat = 14
    private static let gap: CGFloat = 4

    static func image(_ spec: MenuBarIconSpec) -> NSImage {
        let badgeFont = NSFont.systemFont(ofSize: 8, weight: .bold)
        let timeFont = NSFont.monospacedSystemFont(ofSize: 8, weight: .bold)

        let leadWidth: CGFloat
        switch spec.glyph {
        case .waveform: leadWidth = 16
        case .dictating: leadWidth = 22
        case .recording(let elapsed): leadWidth = 5 + 5 + 3 + textWidth(elapsed, timeFont) + 5
        }
        let badgeWidth = spec.badge.map { textWidth($0, badgeFont, tracking: 0.8) + 10 }
        let width = leadWidth + (badgeWidth.map { gap + $0 } ?? 0)

        let image = NSImage(size: NSSize(width: width, height: height), flipped: false) { _ in
            guard let context = NSGraphicsContext.current?.cgContext else { return true }
            NSColor.black.set()
            let midY = height / 2
            switch spec.glyph {
            case .waveform:
                drawWaveform()
            case .recording(let elapsed):
                let rect = NSRect(x: 0, y: midY - pillHeight / 2, width: leadWidth, height: pillHeight)
                outline(rect)
                NSBezierPath(ovalIn: NSRect(x: 5, y: midY - 2.5, width: 5, height: 5)).fill()
                draw(elapsed, font: timeFont, at: NSPoint(x: 5 + 5 + 3, y: midY), tracking: 0, in: context, knockout: false)
            case .dictating:
                let rect = NSRect(x: 0, y: midY - pillHeight / 2, width: leadWidth, height: pillHeight)
                NSBezierPath(roundedRect: rect, xRadius: pillHeight / 2, yRadius: pillHeight / 2).fill()
                if let mic = NSImage(systemSymbolName: "mic.fill", accessibilityDescription: nil) {
                    let icon = NSRect(x: rect.midX - 4.5, y: midY - 4.5, width: 9, height: 9)
                    mic.draw(in: icon, from: .zero, operation: .destinationOut, fraction: 1)
                }
            }
            if let badge = spec.badge, let badgeWidth {
                let rect = NSRect(x: leadWidth + gap, y: midY - pillHeight / 2, width: badgeWidth, height: pillHeight)
                if spec.badgeFilled {
                    NSBezierPath(roundedRect: rect, xRadius: pillHeight / 2, yRadius: pillHeight / 2).fill()
                } else {
                    outline(rect)
                }
                draw(badge, font: badgeFont, at: NSPoint(x: rect.minX + 5, y: midY), tracking: 0.8, in: context, knockout: spec.badgeFilled)
            }
            return true
        }
        image.isTemplate = true
        image.accessibilityDescription = spec.accessibilityLabel
        return image
    }

    // MARK: Drawing

    /// Four-bar waveform from the design: bars at x 4, 8, 12 with square caps, in a 16pt box.
    private static func drawWaveform() {
        let path = NSBezierPath()
        path.lineWidth = 1.5
        path.lineCapStyle = .square
        for (x, from, to) in [(4.0, 6.5, 9.5), (8.0, 3.5, 12.5), (12.0, 5.5, 10.5)] as [(CGFloat, CGFloat, CGFloat)] {
            // The design's y runs down from the top; AppKit's runs up from the bottom.
            path.move(to: NSPoint(x: x, y: height - from))
            path.line(to: NSPoint(x: x, y: height - to))
        }
        path.stroke()
    }

    private static func outline(_ rect: NSRect) {
        let path = NSBezierPath(
            roundedRect: rect.insetBy(dx: 0.625, dy: 0.625), xRadius: pillHeight / 2, yRadius: pillHeight / 2
        )
        path.lineWidth = 1.25
        path.stroke()
    }

    /// Draws `text` vertically centred on `origin.y`; `knockout` cuts the letters out of what is beneath.
    private static func draw(
        _ text: String, font: NSFont, at origin: NSPoint, tracking: CGFloat, in context: CGContext, knockout: Bool
    ) {
        let string = NSAttributedString(string: text, attributes: [
            .font: font, .foregroundColor: NSColor.black, .kern: tracking,
        ])
        // draw(at:) takes the bottom of the line, which sits a descender below the baseline.
        let point = NSPoint(x: origin.x, y: origin.y - font.capHeight / 2 + font.descender)
        context.saveGState()
        if knockout { context.setBlendMode(.destinationOut) }
        string.draw(at: point)
        context.restoreGState()
    }

    private static func textWidth(_ text: String, _ font: NSFont, tracking: CGFloat = 0) -> CGFloat {
        ceil(NSAttributedString(string: text, attributes: [.font: font, .kern: tracking]).size().width)
    }
}
