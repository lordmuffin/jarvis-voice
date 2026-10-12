import AppKit

/// The menu-bar icon: the state symbol plus a tag naming the server's channel. LAB is a filled
/// pill with the letters cut out and LIVE an outlined one, so they differ at a glance whether the
/// menu bar is light or dark.
enum MenuBarIcon {
    /// `color` nil draws a template image that follows the menu bar; otherwise symbol and tag
    /// share that colour (recording, dictating).
    static func image(symbol: NSImage, tag: String?, filled: Bool, color: NSColor?) -> NSImage {
        guard let tag, !tag.isEmpty else { return symbol }
        let textWidth = ceil(NSAttributedString(string: tag, attributes: [.font: tagFont]).size().width)
        let pill = NSSize(width: textWidth + 6, height: 12)
        let gap: CGFloat = 3
        let size = NSSize(width: symbol.size.width + gap + pill.width, height: max(symbol.size.height, pill.height))
        let image = NSImage(size: size, flipped: false) { _ in
            let font = tagFont
            let ink = color ?? .black
            symbol.draw(in: NSRect(
                x: 0, y: (size.height - symbol.size.height) / 2,
                width: symbol.size.width, height: symbol.size.height
            ))
            let rect = NSRect(
                x: symbol.size.width + gap, y: (size.height - pill.height) / 2,
                width: pill.width, height: pill.height
            )
            let path = NSBezierPath(roundedRect: rect.insetBy(dx: 0.5, dy: 0.5), xRadius: 3, yRadius: 3)
            let text = NSAttributedString(string: tag, attributes: [.font: font, .foregroundColor: ink])
            // Centre the capitals: draw(at:) takes the bottom of the line, a descender below the baseline.
            let origin = NSPoint(x: rect.minX + 3, y: rect.midY - font.capHeight / 2 + font.descender)
            ink.set()
            if filled {
                path.fill()
                guard let context = NSGraphicsContext.current?.cgContext else { return true }
                context.saveGState()
                context.setBlendMode(.destinationOut)
                text.draw(at: origin)
                context.restoreGState()
            } else {
                path.lineWidth = 1
                path.stroke()
                text.draw(at: origin)
            }
            return true
        }
        image.isTemplate = color == nil
        image.accessibilityDescription = "Jarvis Voice, \(tag) server"
        return image
    }

    private static var tagFont: NSFont { .systemFont(ofSize: 9, weight: .bold) }
}
