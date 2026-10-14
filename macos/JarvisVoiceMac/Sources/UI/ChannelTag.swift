import JarvisLiveKit
import SwiftUI

/// The server's channel as a small filled badge, coloured like the dashboard's wordmark tag: LIVE in
/// red (error), LAB in amber (warning), anything else grey, so live is never mistaken for lab.
struct ChannelTag: View {
    let channel: ServerChannel

    var body: some View {
        Text(channel.tag)
            .font(.caption2.weight(.bold))
            .tracking(0.6)
            .padding(.horizontal, 5)
            .padding(.vertical, 1)
            .foregroundStyle(JV.bg)
            .background(fill, in: RoundedRectangle(cornerRadius: 3))
    }

    private var fill: Color {
        switch channel {
        case .live: JV.error
        case .lab: JV.warning
        default: JV.text2
        }
    }
}
