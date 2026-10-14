import { releaseChannel } from "../store";

/** "Jarvis Live", plus a tag naming the release channel: red LIVE on live, amber LAB on lab,
 * grey for anything else (a dev server), so an unstable server is never mistaken for the real
 * one or vice versa. The same tag as the Mac and Android apps (design/README.md, Color). */
export function Wordmark() {
  const channel = releaseChannel.value;
  const live = channel === "live";
  const style = live ? " release-channel--live" : channel === "lab" ? " release-channel--lab" : "";
  return (
    <span class="wordmark">
      Jarvis Live
      {channel && (
        <span
          class={`release-channel${style}`}
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
