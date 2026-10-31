import type { Page, Route, WebSocketRoute } from "@playwright/test";

// A scripted live session: the ARC runner vendor call from the design artboards. REST calls
// (session, ticket, end, work items) are answered here and the viewer socket is a mock whose
// messages the test sends, so every state of the feed is reproducible. Nothing reaches the real
// server except the sign-in that gets the page this far.

export const LIVE_ID = "11111111-2222-4333-8444-555555555555";

const seg = (id: string, ms: number, who: "me" | "them", text: string) => ({
  type: "segment",
  id,
  channel: who === "me" ? "mic" : "system",
  speaker: who,
  start_ms: ms,
  end_ms: ms + 4000,
  text,
  stt_tier: "pve",
});

const draft = (ms: number, who: "me" | "them", text: string) => ({
  type: "draft_segment",
  channel: who === "me" ? "mic" : "system",
  start_ms: ms,
  end_ms: ms + 3000,
  text,
  final: false,
});

/** Everything said up to 18:05, with the first findings (these are "already there"). */
export const BACKLOG = [
  seg("s1", 838_000, "them", "We run the controller in its own namespace. Scale sets register per repository or per org."),
  seg("s2", 860_000, "me", "Org level works for us. Four repos need runners."),
  seg("s3", 872_000, "me", "What's the timeline for the ARM images?"),
  seg("s4", 891_000, "them", "ARM is in preview. I can't commit to a date on this call."),
  seg("s5", 940_000, "them", "We can support ARC scale sets, but GPU runners are a separate SKU."),
  { type: "marker", t_ms: 965_000, label: "Pricing" },
  seg("s6", 972_000, "me", "Send me the GPU runner pricing and we'll size it against the DGX node."),
  seg("s7", 1_000_000, "them", "Sure. Can you send the node inventory so we can quote?"),
  seg("s8", 1_022_000, "me", "I'll send it by Friday."),
  seg("s9", 1_050_000, "me", "Let's pilot on the k3s2 cluster first and keep the VM runners as a fallback."),
  seg("s10", 1_085_000, "them", "That works. We'd want read access to the cluster metrics during the pilot."),
  {
    type: "copilot",
    version: 1,
    notes: [{ id: "n1", text: "No date yet for the ARM images." }],
    actions: [{ id: "a1", text: "Confirm GPU runner pricing.", owner: "Vendor", due: null }],
    decisions: [],
    suggestions: [{ id: "g1", kind: "gap", text: "You haven't discussed egress costs.", expires_at_ms: 9_000_000 }],
    related: [
      {
        path: "10 Projects/Jarvis/Infrastructure",
        title: "DGX Spark K3s Node — Architecture Capture",
        snippet: "The Spark joins k3s2 as an agent. GPU roles swap by profile.",
        uri: "obsidian://open?vault=vault&file=DGX",
      },
    ],
  },
  { type: "status", stt_tier: "pve", llm_ok: true, lag_ms: 840 },
];

/** Drafts still settling at the bottom of the feed. */
export const DRAFTS = [
  draft(1_111_000, "me", "Metrics are fine, but nothing with write access to the"),
  draft(1_118_000, "them", "Understood, read-only is"),
];

/** What the copilot finds a little later: these are new since you looked. */
export const FRESH = {
  type: "copilot",
  version: 2,
  notes: [{ id: "n1", text: "No date yet for the ARM images." }],
  actions: [
    { id: "a1", text: "Confirm GPU runner pricing.", owner: "Vendor", due: null },
    { id: "a2", text: "Send the node inventory.", owner: "Me", due: "Fri 16 Oct" },
  ],
  decisions: [{ id: "d1", text: "Pilot on the k3s2 cluster first." }],
  suggestions: [
    { id: "g1", kind: "gap", text: "You haven't discussed egress costs.", expires_at_ms: 9_000_000 },
    { id: "q1", kind: "question", text: "Ask whether ARM images are on the public roadmap.", expires_at_ms: 9_000_000 },
  ],
  related: [
    {
      path: "10 Projects/Jarvis/Infrastructure",
      title: "DGX Spark K3s Node — Architecture Capture",
      snippet: "The Spark joins k3s2 as an agent. GPU roles swap by profile.",
      uri: "obsidian://open?vault=vault&file=DGX",
    },
  ],
};

const WORK_ITEM = {
  id: "w1",
  session_id: LIVE_ID,
  session_title: "ARC runner migration vendor call",
  state: "draft",
  title: "Add ARM runner scale set to k3s2",
  repo: "lordmuffin/np-home-homelab",
  intent: "Give the k3s2 cluster an ARC runner scale set for ARM builds, scoped to the org.",
  acceptance_criteria: ["A runner scale set named arm64 registers with the org."],
  done_when: "A test workflow on lordmuffin/np-home-homelab passes on arm64.",
  sources: [{ segment_id: "s3", start_ms: 872_000, text: "What's the timeline for the ARM images?" }],
  problems: [],
  dispatch: null,
};

export interface LiveMock {
  /** Send server messages to the open viewer socket. */
  send: (...events: object[]) => void;
  /** Requests the page made to end the session. */
  ended: () => number;
  /** Resolves when the page has opened its n-th viewer socket (1 = first, 2 = after a reload). */
  waitForSocket: (n: number) => Promise<void>;
}

/**
 * Answer the live session's REST calls and its socket. `ownerId` is the signed-in user (so it
 * is "your" session) unless `owner` says otherwise.
 */
export async function mockLive(
  page: Page,
  { ownerId, mine = true }: { ownerId: string; mine?: boolean },
): Promise<LiveMock> {
  const owner = mine ? { id: ownerId, display_name: "Root Admin" } : { id: "someone-else", display_name: "Sam" };
  let status = "live";
  let endCount = 0;
  const detail = () => ({
    id: LIVE_ID,
    title: "ARC runner migration vendor call",
    mode: "meeting",
    status,
    channels: ["mic", "system"],
    started_at: "2026-10-14T15:05:00Z",
    ended_at: null,
    local_only: false,
    restricted: false,
    streaming: status === "live",
    owner,
    segments: [],
    copilot: { version: 0, notes: [], actions: [], decisions: [], suggestions: [], related: [] },
    markers: [],
    final_note: null,
    audio: {},
    publication: null,
    can_publish: false,
    can_regenerate: false,
  });
  const json = (route: Route, body: unknown, code = 200) =>
    route.fulfill({ status: code, contentType: "application/json", body: JSON.stringify(body) });

  await page.route(`**/v1/sessions/${LIVE_ID}`, (r) => json(r, detail()));
  await page.route(`**/v1/sessions/${LIVE_ID}/ticket`, (r) => json(r, { ticket: "t", expires_in: 60 }));
  await page.route(`**/v1/sessions/${LIVE_ID}/end`, (r) => {
    endCount += 1;
    status = "finalizing";
    return json(r, detail());
  });
  await page.route(`**/v1/sessions/${LIVE_ID}/work-items`, (r) =>
    json(r, { enabled: true, dispatch_enabled: false, repos: [{ name: "lordmuffin/np-home-homelab" }], items: [WORK_ITEM] }),
  );

  let socket: WebSocketRoute | null = null;
  let sockets = 0;
  const waiters: { n: number; resolve: () => void }[] = [];
  await page.routeWebSocket(new RegExp(`/v1/sessions/${LIVE_ID}/stream`), (ws) => {
    socket = ws;
    sockets += 1;
    ws.send(JSON.stringify({ type: "hello_ack", session: LIVE_ID, acked: { mic: null, system: null } }));
    for (const w of waiters.filter((x) => x.n <= sockets)) w.resolve();
  });

  return {
    send: (...events) => {
      for (const e of events) socket?.send(JSON.stringify(e));
    },
    ended: () => endCount,
    waitForSocket: (n) =>
      sockets >= n ? Promise.resolve() : new Promise<void>((resolve) => waiters.push({ n, resolve })),
  };
}

export const PAST_ID = "99999999-8888-4777-8666-555555555555";

const NOTE = `---
session_id: ${PAST_ID}
title: ARC runner migration vendor call
---

# ARC runner migration vendor call

The vendor supports **ARC scale sets** on Kubernetes, with the controller in its own namespace. We agreed to pilot on \`k3s2\` before moving anything else. GPU runners are a separate SKU, and the ARM images have no date yet.

## Decisions
- Pilot on the **k3s2** cluster first. The VM runners stay as a fallback.
- Register scale sets at the org level, not per repository.
- The vendor gets read-only access to cluster metrics during the pilot.

## Actions
- [ ] Send the node inventory. (Me, Fri 16 Oct)
- [ ] Confirm GPU runner pricing. (Vendor)
- [x] List the four repos that need runners.

## Open questions
- When do the ARM images leave preview?
- What does egress cost during the pilot?

## Related
- [[DGX Spark K3s Node — Architecture Capture]]
- [[GitHub ARC on k3s — Build Spec]]
`;

/** A finished session with a note, an open pull request, recordings and a transcript. */
export async function mockPast(page: Page, { ownerId }: { ownerId: string }): Promise<void> {
  const segments = BACKLOG.filter((e): e is ReturnType<typeof seg> => (e as { type: string }).type === "segment").map(
    ({ type: _type, ...rest }) => rest,
  );
  const publication = {
    state: "open",
    repo_path: "Sessions/2026-10-14 ARC runner migration vendor call.md",
    branch: "note/arc-runner",
    pr_number: 212,
    pr_url: "https://github.com/lordmuffin/obsidian-vault/pull/212",
    last_error: null,
    updated_at: "2026-10-14T16:10:00Z",
    merged_at: null,
    changed: true,
  };
  const detail = {
    id: PAST_ID,
    title: "ARC runner migration vendor call",
    mode: "meeting",
    status: "done",
    channels: ["mic", "system"],
    started_at: "2026-10-14T15:05:00Z",
    ended_at: "2026-10-14T15:35:00Z",
    local_only: false,
    restricted: false,
    streaming: false,
    owner: { id: ownerId, display_name: "Root Admin" },
    segments,
    // What the server reads back from NOTE's Actions and Decisions (finalize/note_items.py).
    copilot: {
      version: 3,
      notes: [],
      actions: [
        { id: "note-action-0", text: "Send the node inventory. (Me, Fri 16 Oct)", owner: null, due: null },
        { id: "note-action-1", text: "Confirm GPU runner pricing. (Vendor)", owner: null, due: null },
        { id: "note-action-2", text: "List the four repos that need runners.", owner: null, due: null },
      ],
      decisions: [
        { id: "note-decision-0", text: "Pilot on the **k3s2** cluster first. The VM runners stay as a fallback." },
        { id: "note-decision-1", text: "Register scale sets at the org level, not per repository." },
        { id: "note-decision-2", text: "The vendor gets read-only access to cluster metrics during the pilot." },
      ],
      suggestions: [],
      related: [],
    },
    markers: [{ t_ms: 965_000, label: "Pricing" }],
    final_note: { path: "Sessions/arc.md", title: "ARC runner migration vendor call", markdown: NOTE },
    audio: { mic: true, system: true },
    publication,
    can_publish: true,
    can_regenerate: true,
  };
  const json = (route: Route, body: unknown) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  await page.route(`**/v1/sessions/${PAST_ID}`, (r) => json(r, detail));
  await page.route(`**/v1/sessions/${PAST_ID}/publication`, (r) => json(r, { publication }));
  await page.route(`**/v1/sessions/${PAST_ID}/work-items`, (r) =>
    json(r, { enabled: true, dispatch_enabled: false, repos: [{ name: "lordmuffin/np-home-homelab" }], items: [{ ...WORK_ITEM, session_id: PAST_ID }] }),
  );
  await page.route(`**/v1/sessions/${PAST_ID}/audio/**`, (r) => r.fulfill({ status: 404 }));
}
