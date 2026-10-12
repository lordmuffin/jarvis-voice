import JarvisLiveKit
import SwiftUI

/// The server's channel as a small badge, coloured like the dashboard's: LAB on warning, LIVE in success.
struct ChannelTag: View {
    let channel: ServerChannel

    var body: some View {
        Text(channel.tag)
            .font(.caption2.weight(.bold))
            .tracking(0.6)
            .padding(.horizontal, 5)
            .padding(.vertical, 1)
            .foregroundStyle(channel == .live ? JV.success : JV.bg)
            .background {
                let shape = RoundedRectangle(cornerRadius: 3)
                if channel == .live {
                    shape.strokeBorder(JV.success, lineWidth: 1)
                } else {
                    shape.fill(channel == .lab ? JV.warning : JV.text2)
                }
            }
    }
}
