import SwiftUI

/// Colours from `design/tokens.json` (dark only). Names match the token names.
enum JV {
    static let bg = Color(hex: 0x0D1117)
    static let surface = Color(hex: 0x161B22)
    static let surface2 = Color(hex: 0x1C2230)
    static let accent = Color(hex: 0x00F5D4)
    static let violet = Color(hex: 0x7B2FD4)
    static let text = Color(hex: 0xE6EDF3)
    static let text2 = Color(hex: 0x8B949E)
    static let success = Color(hex: 0x3FB950)
    static let warning = Color(hex: 0xD29922)
    static let error = Color(hex: 0xF85149)
    static let border = Color.white.opacity(0.14)
}

extension Color {
    init(hex: UInt32) {
        self.init(
            red: Double((hex >> 16) & 0xFF) / 255,
            green: Double((hex >> 8) & 0xFF) / 255,
            blue: Double(hex & 0xFF) / 255
        )
    }
}
