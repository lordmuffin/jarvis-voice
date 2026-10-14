import type { ComponentChildren } from "preact";
import { ChannelBadge } from "./ChannelBadge";

/** "Jarvis Live", typeset (15/700), with the release channel badge after it. */
export function Brand({
  channel,
  href,
  onNavigate,
  heading,
}: {
  channel: string | null;
  /** Makes the wordmark a link to the app's first tab. */
  href?: string;
  onNavigate?: (e: MouseEvent) => void;
  /** Wrap in an h1 (sign-in screen). */
  heading?: boolean;
}) {
  const mark: ComponentChildren = href ? (
    <a class="i-wordmark" href={href} onClick={onNavigate}>
      Jarvis Live
    </a>
  ) : (
    <span class="i-wordmark">Jarvis Live</span>
  );
  const body = (
    <>
      {mark}
      <ChannelBadge channel={channel} />
    </>
  );
  return heading ? <h1 class="d-brand">{body}</h1> : <>{body}</>;
}

/** Text only screen readers get. */
export function VisuallyHidden({ children }: { children: ComponentChildren }) {
  return <span class="i-sr">{children}</span>;
}
