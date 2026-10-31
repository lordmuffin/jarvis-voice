import type { SpeakerInfo } from "./types";

/** How a line is labelled, and so how it looks: the owner, a named voice, or an unnamed one. */
export type Who = { text: string; kind: "me" | "them" | "named" | "unknown" };

type Side = "me" | "them";

const SIDE: Record<Side, Who> = {
  me: { text: "Me", kind: "me" },
  them: { text: "Them", kind: "them" },
};

/**
 * The label for a line on `side` spoken by `speakerId`. The same rule as the server's notes: the
 * owner is Me; a named voice shows its name; an unnamed one is "Speaker N" only where its side
 * holds several voices (with one, the side says enough). `voices` counts the distinct voices
 * heard on each side.
 */
export function whoIs(
  side: Side,
  speakerId: string | null | undefined,
  speakers: Record<string, SpeakerInfo>,
  voices: Record<Side, number>,
): Who {
  const sp = speakerId ? speakers[speakerId] : undefined;
  if (!sp) return SIDE[side];
  if (sp.is_owner) return SIDE.me;
  if (sp.label) return { text: sp.label, kind: "named" };
  if (voices[side] > 1 && sp.ordinal !== undefined)
    return { text: `Speaker ${sp.ordinal}`, kind: "unknown" };
  return SIDE[side];
}
