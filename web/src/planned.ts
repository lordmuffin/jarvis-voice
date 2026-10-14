/**
 * Slots the design marks "planned" and keeps a place for ("Paused is planned. Its slot is
 * kept."). The components exist and are tested; nothing shows them until the feature behind
 * them does. Flip a flag here when it lands.
 *
 * Not here: notification settings (Android-only in the design) and Pause / Resume buttons
 * (there is no web artboard for them), so there is nothing to scaffold.
 */
export const PLANNED = {
  /** A live session pinned above the Sessions list (`Jarvis-Comp-Session`, "Pinned live card"). */
  pinnedLive: false,
} as const;
