// @vitest-environment happy-dom
import { render } from "preact";
import { act } from "preact/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ConnectionChip } from "../src/jarvis/ConnectionChip";
import { CopilotItem, FinalNoteRow } from "../src/jarvis/CopilotItem";
import { dockView } from "../src/jarvis/dock";
import { FeedFilter } from "../src/jarvis/FeedFilter";
import { JumpPill } from "../src/jarvis/JumpPill";
import { MarkerRow } from "../src/jarvis/MarkerRow";
import { NowDock } from "../src/jarvis/NowDock";
import { RelatedNoteCard } from "../src/jarvis/RelatedNoteCard";
import { SessionHeader } from "../src/jarvis/SessionHeader";
import { SessionRow } from "../src/jarvis/SessionRow";
import { Timeline } from "../src/jarvis/Timeline";
import { TranscriptLine } from "../src/jarvis/TranscriptLine";
import { UploadForm } from "../src/jarvis/UploadForm";
import { me } from "../src/store";
import type { Line } from "../src/reducer";
import type { SessionSummary } from "../src/types";

afterEach(() => {
  render(null, document.body);
  document.body.innerHTML = "";
  me.value = "loading";
  vi.restoreAllMocks();
});

const q = (sel: string) => document.querySelector(sel);
const qa = (sel: string) => [...document.querySelectorAll(sel)];

const line = (over: Partial<Line> = {}): Line => ({
  kind: "segment",
  key: "k1",
  id: "seg1",
  sortMs: 872_000,
  start_ms: 872_000,
  end_ms: 874_000,
  speaker: "me",
  text: "What's the timeline for the ARM images?",
  ...over,
});

describe("TranscriptLine", () => {
  it("says who in words (a label), with the time, and is a segment", () => {
    render(<TranscriptLine line={line()} />, document.body);
    expect(q(".i-spk")!.textContent).toBe("Me");
    expect(q(".t")!.textContent).toBe("14:32");
    expect(q("[data-testid=segment]")).not.toBeNull();
    render(<TranscriptLine line={line({ speaker: "them" })} />, document.body);
    expect(q(".i-spk")!.textContent).toBe("Them");
    expect(q(".i-utt--them")).not.toBeNull();
  });

  it("marks a draft as one, to screen readers too, without colour", () => {
    render(<TranscriptLine line={line({ kind: "draft", text: "Metrics are fine, but" })} />, document.body);
    const row = q("[data-testid=draft]")!;
    expect(row.classList.contains("i-utt--draft")).toBe(true);
    expect(row.querySelector(".i-sr")!.textContent).toContain("draft");
  });

  it("swaps a draft for its final in the same DOM node, so nothing moves", async () => {
    render(<TranscriptLine line={line({ kind: "draft", key: "draft-mic-1", text: "Let's pilot on the" })} />, document.body);
    const before = q("[data-key=draft-mic-1]")!;
    render(
      <TranscriptLine line={line({ kind: "segment", key: "draft-mic-1", text: "Let's pilot on the k3s2 cluster first." })} />,
      document.body,
    );
    const after = q("[data-key=draft-mic-1]")!;
    expect(after).toBe(before);
    expect(after.querySelector(".x")!.textContent).toBe("Let's pilot on the k3s2 cluster first.");
    expect(after.classList.contains("i-utt--draft")).toBe(false);
    expect(after.getAttribute("data-testid")).toBe("segment");
  });

  it("never gets shorter than the draft was", () => {
    let natural = 60;
    vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockImplementation(function (this: HTMLElement) {
      return this.style.minHeight ? Number.parseInt(this.style.minHeight, 10) : natural;
    });
    render(<TranscriptLine line={line({ kind: "draft", key: "d", text: "a long draft that wrapped onto three lines" })} />, document.body);
    natural = 24; // the final is shorter
    render(<TranscriptLine line={line({ kind: "segment", key: "d", text: "Short." })} />, document.body);
    expect((q("[data-key=d]") as HTMLElement).style.minHeight).toBe("60px");
  });

  it("highlights the line a work item points at", () => {
    render(<TranscriptLine line={line()} target />, document.body);
    expect(q(".i-utt--target")!.getAttribute("aria-current")).toBe("true");
  });
});

describe("MarkerRow", () => {
  it("shows the label and time, and reads Marker with no label", () => {
    render(<MarkerRow marker={{ t_ms: 965_000, label: "Pricing" }} />, document.body);
    expect(q(".j-marker")!.textContent).toContain("Pricing");
    expect(q(".j-marker")!.textContent).toContain("16:05");
    render(<MarkerRow marker={{ t_ms: 1_000, label: "" }} />, document.body);
    expect(q(".j-marker")!.textContent).toContain("Marker");
  });
});

describe("CopilotItem", () => {
  const action = { key: "action:1", kind: "action" as const, text: "Send the node inventory.", meta: "Me · Fri 16 Oct" };

  it("is a feed row with the time it arrived, its kind and glyph", () => {
    render(<CopilotItem insight={action} variant="event" at={1_040_000} />, document.body);
    const row = q("[data-testid=copilot-action]")!;
    expect(row.classList.contains("i-event")).toBe(true);
    expect(row.querySelector(".t")!.textContent).toBe("17:20");
    expect(row.querySelector(".i-chip")!.textContent).toBe("Action");
    expect(row.textContent).toContain("☐");
    expect(row.textContent).toContain("Me · Fri 16 Oct");
  });

  it("tints and tags a new one, and does not for the rest", () => {
    render(<CopilotItem insight={action} variant="event" isNew />, document.body);
    expect(q(".i-fresh")).not.toBeNull();
    expect(q(".i-new")!.textContent).toBe("New");
    render(<CopilotItem insight={action} variant="event" />, document.body);
    expect(q(".i-fresh")).toBeNull();
    expect(q(".i-new")).toBeNull();
  });

  it("is a compact row in a grouped list", () => {
    render(<CopilotItem insight={{ key: "decision:1", kind: "decision", text: "Pilot on k3s2." }} variant="item" />, document.body);
    expect(q(".j-item")!.textContent).toContain("✓");
  });

  it("links a work item's source into the transcript", () => {
    render(<CopilotItem insight={{ key: "work:1", kind: "work", text: "Add ARM", srcMs: 872_000, srcText: "ARM?", tag: "Draft" }} variant="event" />, document.body);
    const a = q(".i-src")!;
    expect(a.getAttribute("href")).toBe("#t=872000");
    expect(a.textContent).toBe("▶ 14:32");
    expect(q(".i-event")!.textContent).toContain("Draft");
  });

  it("opens a related note only through obsidian://", () => {
    render(
      <CopilotItem insight={{ key: "related:a", kind: "related", text: "DGX", href: "obsidian://open?vault=v" }} variant="event" />,
      document.body,
    );
    expect(q("a")!.getAttribute("href")).toBe("obsidian://open?vault=v");
  });

  it("shows the final note", () => {
    render(<FinalNoteRow note={{ path: "p", title: "ARC call" }} />, document.body);
    expect(q("[data-testid=final-note]")!.textContent).toBe("ARC call");
  });
});

describe("RelatedNoteCard", () => {
  it("shows title, path and snippet, and links only obsidian://", () => {
    render(<RelatedNoteCard related={{ path: "10/DGX.md", title: "DGX", snippet: "The Spark joins k3s2.", uri: "https://evil.example" }} />, document.body);
    expect(q(".j-related")!.textContent).toContain("10/DGX.md");
    expect(q(".snip")!.textContent).toBe("The Spark joins k3s2.");
    expect(q("a")).toBeNull();
    render(<RelatedNoteCard related={{ path: "p", title: "T", snippet: "s", uri: "obsidian://open?x" }} />, document.body);
    expect(q("a")!.getAttribute("href")).toBe("obsidian://open?x");
  });
});

describe("NowDock", () => {
  const base = { suggestions: [], dismissed: new Set<string>(), holds: {}, version: 1, streamMs: 100_000, arrivals: {}, lookedAt: 0 };
  const sug = (id: string, text: string, expires = 400_000) => ({ id, kind: "question" as const, text, expires_at_ms: expires });
  const noop = () => undefined;

  it("is a labelled Now region and says so when empty", () => {
    render(<NowDock view={dockView(base)} onDismiss={noop} onUndo={noop} />, document.body);
    expect(q("[role=region][aria-label=Now]")).not.toBeNull();
    expect(q(".i-dock--quiet")).not.toBeNull();
    expect(q(".txt")!.textContent).toBe("Nothing waiting. Suggestions show up here.");
  });

  it("leads with a new suggestion, tinted, with age and a way to dismiss it", () => {
    const view = dockView({
      ...base,
      suggestions: [sug("q", "Ask whether ARM images are on the public roadmap.")],
      arrivals: { "suggestion:q": { at: 78_000, initial: false, wall: 78_000 } },
    });
    const onDismiss = vi.fn();
    render(<NowDock view={view} onDismiss={onDismiss} onUndo={noop} />, document.body);
    expect(q(".i-dock")!.classList.contains("i-fresh")).toBe(true);
    expect(q(".txt")!.textContent).toContain("ARM images");
    expect(q(".i-new")!.textContent).toBe("· New");
    expect(q(".hd .t-mono")!.textContent).toBe("0:22");
    (q("[aria-label='Dismiss suggestion']") as HTMLElement).click();
    expect(onDismiss).toHaveBeenCalledWith("q");
  });

  it("puts an older suggestion on one line below, ageing, with its own dismiss", () => {
    const view = dockView({
      ...base,
      suggestions: [sug("a", "Older."), sug("b", "Newer.")],
      arrivals: { "suggestion:a": { at: 10_000, initial: true, wall: 10_000 }, "suggestion:b": { at: 99_000, initial: true, wall: 99_000 } },
    });
    render(<NowDock view={view} onDismiss={noop} onUndo={noop} />, document.body);
    expect(qa("[data-testid=suggestion]").map((e) => e.getAttribute("data-state"))).toEqual(["ageing", "ageing"]);
    expect(q(".more")!.textContent).toContain("Older.");
    expect(qa("[aria-label='Dismiss suggestion']")).toHaveLength(2);
  });

  it("labels an expired suggestion and offers no dismiss", () => {
    const view = dockView({ ...base, suggestions: [sug("x", "Gap.", 90_000)], holds: { x: { kind: "expired", version: 1 } } });
    render(<NowDock view={view} onDismiss={noop} onUndo={noop} />, document.body);
    expect(q("[data-state=expired]")!.textContent).toContain("Expired");
    expect(q("[aria-label='Dismiss suggestion']")).toBeNull();
  });

  it("shows a dismissed suggestion quiet, with Undo", () => {
    const view = dockView({ ...base, suggestions: [sug("q", "x")], dismissed: new Set(["q"]), holds: { q: { kind: "dismissed", version: 1 } } });
    const onUndo = vi.fn();
    render(<NowDock view={view} onDismiss={noop} onUndo={onUndo} />, document.body);
    expect(q(".i-dock--quiet")).not.toBeNull();
    expect(q(".hd")!.textContent).toContain("Dismissed · Question");
    [...document.querySelectorAll("button")].find((b) => b.textContent?.includes("Undo"))!.click();
    expect(onUndo).toHaveBeenCalledWith("q");
  });

  it("waits a dismissed one below the next suggestion, with Undo", () => {
    const view = dockView({
      ...base,
      suggestions: [sug("q", "Dismissed one"), sug("r", "Next one")],
      dismissed: new Set(["q"]),
      holds: { q: { kind: "dismissed", version: 1 } },
      arrivals: { "suggestion:q": { at: 90_000, initial: true, wall: 90_000 }, "suggestion:r": { at: 80_000, initial: true, wall: 80_000 } },
    });
    render(<NowDock view={view} onDismiss={noop} onUndo={noop} />, document.body);
    expect(q(".txt")!.textContent).toBe("Next one");
    expect(q(".more")!.textContent).toContain("Dismissed");
    expect(q(".more")!.textContent).toContain("Undo");
  });

  it("says the copilot is offline, or local only, when there is nothing to show", () => {
    render(<NowDock view={dockView(base)} copilotOffline onDismiss={noop} onUndo={noop} />, document.body);
    expect(q(".hd")!.textContent).toContain("Copilot degraded");
    render(<NowDock view={dockView(base)} localOnly onDismiss={noop} onUndo={noop} />, document.body);
    expect(q(".hd")!.textContent).toContain("Local-only");
  });
});

describe("ConnectionChip", () => {
  const chip = () => q("[data-testid=connection]")!;
  const status = { stt_tier: "pve", llm_ok: true, lag_ms: 840 };

  it("names each state in words", () => {
    for (const [c, t] of [
      ["idle", "Offline"],
      ["connecting", "Reconnecting…"],
      ["open", "Live"],
      ["closed", "Reconnecting…"],
    ] as const) {
      render(<ConnectionChip connection={c} status={null} />, document.body);
      expect(chip().textContent).toBe(t);
    }
  });

  it("is live-coloured with a dot when connected, amber with a spinner when reconnecting", () => {
    render(<ConnectionChip connection="open" status={null} />, document.body);
    expect(chip().classList.contains("i-chip--live")).toBe(true);
    expect(chip().querySelector(".i-dot")).not.toBeNull();
    render(<ConnectionChip connection="closed" status={null} />, document.body);
    expect(chip().classList.contains("i-chip--progress")).toBe(true);
    expect(chip().querySelector(".i-spin")).not.toBeNull();
  });

  it("shows tier and lag quietly, and the copilot being offline", () => {
    render(<ConnectionChip connection="open" status={status} copilotOffline />, document.body);
    expect(document.body.textContent).toContain("pve · lag 840 ms");
    expect(document.body.textContent).toContain("copilot degraded");
    render(<ConnectionChip connection="open" status={{ ...status, stt_tier: null }} />, document.body);
    expect(document.body.textContent).toContain("— · lag 840 ms");
    expect(document.body.textContent).not.toContain("copilot degraded");
  });

  it("has the degraded and queued shells for fields the server doesn't send yet", () => {
    render(<ConnectionChip connection="open" status={null} degraded="gamingpc" />, document.body);
    expect(chip().textContent).toBe("⚑ Degraded · gamingpc");
    render(<ConnectionChip connection="closed" status={null} queuedMs={3200} />, document.body);
    expect(chip().textContent).toBe("Reconnecting… · 3.2 s queued");
  });
});

describe("FeedFilter", () => {
  it("counts each kind and reports a choice", () => {
    const onChange = vi.fn();
    render(
      <FeedFilter
        value="all"
        onChange={onChange}
        insights={[
          { key: "a1", kind: "action", text: "a" },
          { key: "a2", kind: "action", text: "b" },
          { key: "d1", kind: "decision", text: "d" },
        ]}
      />,
      document.body,
    );
    const radios = qa("[role=radio]");
    expect(radios.map((r) => r.textContent)).toEqual(["All", "Actions 2", "Decisions 1", "Notes 0", "Related 0", "Work items 0"]);
    (radios[1] as HTMLElement).click();
    expect(onChange).toHaveBeenCalledWith("action");
  });
});

describe("JumpPill", () => {
  it("counts what is new, and jumps", () => {
    const onJump = vi.fn();
    render(<JumpPill count={3} onJump={onJump} />, document.body);
    expect(q("button")!.textContent).toBe("↓ Jump To Latest · 3 new");
    (q("button") as HTMLElement).click();
    expect(onJump).toHaveBeenCalled();
    render(<JumpPill count={0} onJump={onJump} />, document.body);
    expect(q("button")!.textContent).toBe("↓ Jump To Latest");
  });
});

describe("SessionHeader", () => {
  const base: SessionSummary = {
    id: "s1",
    title: "Vendor call",
    mode: "meeting",
    status: "done",
    channels: ["mic"],
    started_at: "2026-10-14T15:05:00Z",
    ended_at: "2026-10-14T15:35:00Z",
    local_only: false,
    restricted: false,
    streaming: false,
    owner: { id: "u1", display_name: "Andrew" },
  };
  const label = (session: SessionSummary) => {
    render(<SessionHeader session={session} title={<span>t</span>} />, document.body);
    return q("[data-testid=session-head] .t-label")!.textContent ?? "";
  };

  it("says a work call is restricted", () => {
    expect(label({ ...base, restricted: true })).toContain("Restricted");
  });

  it("says nothing for an ordinary session", () => {
    expect(label(base)).not.toContain("Restricted");
  });
});

describe("SessionRow", () => {
  const session: SessionSummary = {
    id: "s1",
    title: "ARC runner migration vendor call",
    mode: "meeting",
    status: "done",
    channels: ["mic"],
    started_at: "2026-10-14T15:05:00Z",
    ended_at: "2026-10-14T15:35:00Z",
    local_only: false,
    restricted: false,
    streaming: false,
    owner: { id: "u1", display_name: "Andrew" },
  };

  it("links to the session with title, length, mode and status", () => {
    me.value = { id: "u1" } as never;
    const onOpen = vi.fn();
    render(<SessionRow session={session} onOpen={onOpen} />, document.body);
    const row = q("[data-testid=session-row]")!;
    expect(row.getAttribute("href")).toBe("/sessions/s1");
    expect(row.textContent).toContain("ARC runner migration vendor call");
    expect(row.textContent).toContain("30 min");
    expect(row.textContent).toContain("meeting");
    expect(row.textContent).toContain("Done");
    expect(q("[data-testid=owner]")).toBeNull();
    (row as HTMLElement).click();
    expect(onOpen).toHaveBeenCalledWith("s1");
  });

  it("names the owner when it isn't yours, and styles an untitled session quietly", () => {
    me.value = { id: "someone-else" } as never;
    render(<SessionRow session={{ ...session, title: null }} onOpen={vi.fn()} />, document.body);
    expect(q("[data-testid=owner]")!.textContent).toContain("Andrew");
    expect(q(".ti--none")!.textContent).toBe("Untitled session");
  });
});

describe("UploadForm", () => {
  const props = {
    file: { name: "vendor-followup.m4a", size: 38_200_000 },
    mode: "solo" as const,
    busy: false,
    error: null,
    onMode: vi.fn(),
    onSubmit: vi.fn(),
    onCancel: vi.fn(),
    onChangeFile: vi.fn(),
  };

  it("shows the file, its size, and a title placeholder from the name", () => {
    render(<UploadForm {...props} />, document.body);
    expect(q("[data-testid=upload-form]")!.textContent).toContain("vendor-followup.m4a");
    expect(q("[data-testid=upload-form]")!.textContent).toContain("36.4 MB");
    expect((q("input[name=title]") as HTMLInputElement).placeholder).toBe("vendor-followup");
  });

  it("chooses the mode with a segmented control", () => {
    const onMode = vi.fn();
    render(<UploadForm {...props} onMode={onMode} />, document.body);
    const radios = qa("[role=radio]") as HTMLElement[];
    expect(radios.map((r) => r.getAttribute("aria-checked"))).toEqual(["true", "false"]);
    radios[1]!.click();
    expect(onMode).toHaveBeenCalledWith("meeting");
  });

  it("disables Upload while busy and offers no Change", () => {
    render(<UploadForm {...props} busy />, document.body);
    const upload = qa("button").find((b) => b.textContent === "Uploading…") as HTMLButtonElement;
    expect(upload.disabled).toBe(true);
    expect(qa("button").some((b) => b.textContent === "Change")).toBe(false);
  });

  it("has shells for progress and a blocked reason (nothing drives them yet)", () => {
    render(<UploadForm {...props} busy progress={{ sent: 24_400_000, total: 38_200_000 }} />, document.body);
    expect(q("[role=progressbar]")!.getAttribute("aria-valuenow")).toBe("64");
    render(<UploadForm {...props} blocked="Recordings are limited to 500 MB." />, document.body);
    expect(q("[role=alert]")!.textContent).toContain("limited to 500 MB");
    expect((qa("button").find((b) => b.textContent === "Upload") as HTMLButtonElement).disabled).toBe(true);
  });
});

describe("Timeline", () => {
  const lines = [line({ key: "a", id: "a", sortMs: 1000, start_ms: 1000, end_ms: 2000, text: "First." }), line({ key: "b", id: "b", sortMs: 3000, start_ms: 3000, end_ms: 4000, speaker: "them", text: "Second." })];

  it("says Nothing yet when empty", () => {
    render(<Timeline label="Transcript" lines={[]} markers={[]} />, document.body);
    expect(q(".d-log")!.textContent).toBe("Nothing yet.");
  });

  it("is the scroller and a polite log when live", () => {
    render(<Timeline live label="Transcript and copilot" lines={lines} markers={[]} />, document.body);
    const log = q("[role=log]")!;
    expect(log.classList.contains("d-log")).toBe(true);
    expect(log.getAttribute("aria-live")).toBe("polite");
    expect(log.querySelectorAll("[data-testid=segment]")).toHaveLength(2);
  });

  it("draws one accent rule before the first new finding, counting the new ones", () => {
    const insights = [
      { key: "action:1", kind: "action" as const, text: "Send it." },
      { key: "decision:1", kind: "decision" as const, text: "Pilot." },
    ];
    const arrivals = { "action:1": { at: 5000, initial: false, wall: 5000 }, "decision:1": { at: 6000, initial: false, wall: 6000 } };
    render(<Timeline live label="x" lines={lines} markers={[]} insights={insights} arrivals={arrivals} look={{ streamMs: 4000, wallMs: 4000 }} />, document.body);
    const rule = q("[data-testid=new-rule]")!;
    expect(rule.textContent).toBe("2 new since you looked at 0:04");
    expect(qa(".i-fresh")).toHaveLength(2);
    // The rule sits right above the first new row.
    expect((rule.nextElementSibling as HTMLElement).getAttribute("data-testid")).toBe("copilot-action");
  });

  it("draws no rule and tints nothing when nothing is new", () => {
    const insights = [{ key: "action:1", kind: "action" as const, text: "Send it." }];
    render(<Timeline live label="x" lines={lines} markers={[]} insights={insights} arrivals={{ "action:1": { at: 5000, initial: true, wall: 5000 } }} look={{ streamMs: 4000, wallMs: 4000 }} />, document.body);
    expect(q("[data-testid=new-rule]")).toBeNull();
    expect(q(".i-fresh")).toBeNull();
  });

  it("shows a grouped list for a chosen filter, in place of the feed", () => {
    const insights = [
      { key: "action:1", kind: "action" as const, text: "Send it." },
      { key: "note:1", kind: "note" as const, text: "No date." },
    ];
    render(<Timeline live label="x" lines={lines} markers={[]} insights={insights} filter="note" />, document.body);
    expect(q("[data-testid=segment]")).toBeNull();
    expect(q(".j-sec")!.textContent).toContain("Notes");
    expect(q("[data-testid=copilot-note]")).not.toBeNull();
    expect(q("[data-testid=copilot-action]")).toBeNull();
  });

  it("shows follow state beside the filter", async () => {
    render(<Timeline live label="x" lines={lines} markers={[]} toolbar={<span>bar</span>} />, document.body);
    await act(() => undefined);
    expect(q("[data-testid=follow-state]")!.textContent).toBe("↓ Following live");
    expect(q(".d-jump")).toBeNull();
  });
});
