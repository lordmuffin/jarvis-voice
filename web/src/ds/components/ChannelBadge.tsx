import { cx } from "../classes";

/** The release channel, always shown: Live (red), Lab (amber), anything else (grey). */
export function ChannelBadge({ channel }: { channel: string | null }) {
  if (!channel) return null;
  const live = channel === "live";
  const lab = channel === "lab";
  return (
    <span
      class={cx("i-badge", live && "i-badge--live", lab && "i-badge--lab")}
      data-testid="release-channel"
      title={live ? "This is the live (production) server" : `This is the ${channel} server, not live`}
    >
      {live ? "Live" : lab ? "Lab" : channel}
    </span>
  );
}
