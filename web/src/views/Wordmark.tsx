import { releaseChannel } from "../store";

/** "Jarvis Live", plus a tag naming the release channel when it isn't live (e.g. LAB), so
 * an unstable server is never mistaken for the real one. */
export function Wordmark() {
  const channel = releaseChannel.value;
  return (
    <span class="wordmark">
      Jarvis Live
      {channel && (
        <span
          class="release-channel"
          data-testid="release-channel"
          title={`This is the ${channel} server, not live`}
        >
          {channel}
        </span>
      )}
    </span>
  );
}
