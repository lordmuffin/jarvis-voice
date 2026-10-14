import { releaseChannel } from "../store";

/** "Jarvis Live", plus a tag naming the release channel: red LIVE on live, amber on anything
 * else (e.g. LAB), so an unstable server is never mistaken for the real one or vice versa. */
export function Wordmark() {
  const channel = releaseChannel.value;
  const live = channel === "live";
  return (
    <span class="wordmark">
      Jarvis Live
      {channel && (
        <span
          class={live ? "release-channel release-channel--live" : "release-channel"}
          data-testid="release-channel"
          title={
            live ? "This is the live (production) server" : `This is the ${channel} server, not live`
          }
        >
          {channel}
        </span>
      )}
    </span>
  );
}
