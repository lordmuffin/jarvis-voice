// @vitest-environment happy-dom
import { render } from "preact";
import { act } from "preact/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DeleteAfter, DeleteSession, DeletedNotice } from "../src/jarvis/DeleteSession";
import { DeletedView } from "../src/views/Deleted";
import type { SessionDetail, SessionSummary } from "../src/types";

const owner = { id: "u1", display_name: "Alice" };
const summary = (over: Partial<SessionSummary> = {}): SessionSummary => ({
  id: "s1",
  title: "Budget review",
  mode: "meeting",
  status: "done",
  channels: ["mic"],
  started_at: "2026-10-01T10:00:00Z",
  ended_at: "2026-10-01T10:30:00Z",
  local_only: false,
  restricted: false,
  streaming: false,
  owner,
  deleted_at: "2026-10-28T10:00:00Z",
  purge_after: null,
  trash_expires_at: "2026-11-27T10:00:00Z",
  ...over,
});
const detail = (over: Partial<SessionDetail> = {}) => ({ ...summary(), ...over }) as SessionDetail;

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

let root: HTMLElement;
const calls: string[] = [];
const bodies: unknown[] = [];

/** Answers by route; records "METHOD url" and the JSON body of each call. */
function serve(routes: Record<string, () => Response>) {
  globalThis.fetch = vi.fn(async (url: string, init: RequestInit = {}) => {
    const key = `${init.method ?? "GET"} ${url}`;
    calls.push(key);
    bodies.push(init.body ? JSON.parse(init.body as string) : undefined);
    const handler = Object.entries(routes).find(([k]) => key.startsWith(k));
    if (!handler) throw new Error(`unexpected ${key}`);
    return handler[1]();
  }) as unknown as typeof fetch;
}

const buttons = () => [...root.querySelectorAll("button")];
const byText = (t: string) => buttons().find((b) => b.textContent?.trim() === t);
// Let the mocked fetch, its json() and the state updates that follow all run.
const settle = async () => {
  for (let i = 0; i < 5; i++) await act(() => new Promise((r) => setTimeout(r, 0)));
};

beforeEach(() => {
  root = document.createElement("div");
  document.body.appendChild(root);
  calls.length = 0;
  bodies.length = 0;
});
afterEach(() => {
  render(null, root);
  document.body.innerHTML = "";
  vi.restoreAllMocks();
});

describe("Recently deleted", () => {
  it("lists the trash with when it was deleted and when it goes for good", async () => {
    serve({ "GET /v1/sessions?": () => json({ items: [summary()], total: 1, limit: 100, offset: 0 }) });
    render(<DeletedView />, root);
    await settle();
    expect(calls[0]).toContain("deleted=true");
    const row = root.querySelector("[data-testid=deleted-row]")!;
    expect(row.textContent).toContain("Budget review");
    expect(row.textContent).toContain("Deleted");
    expect(row.textContent).toContain("Removed for good");
    expect(byText("Restore")).toBeDefined();
  });

  it("restores one session and drops it from the list", async () => {
    serve({
      "GET /v1/sessions?": () =>
        json({ items: [summary(), summary({ id: "s2", title: "Standup" })], total: 2, limit: 100, offset: 0 }),
      "POST /v1/sessions/s1/restore": () => json(summary({ deleted_at: null })),
    });
    render(<DeletedView />, root);
    await settle();
    act(() => byText("Restore")!.click());
    await settle();
    expect(calls).toContain("POST /v1/sessions/s1/restore");
    const rows = [...root.querySelectorAll("[data-testid=deleted-row]")];
    expect(rows.map((r) => r.textContent)).toEqual([expect.stringContaining("Standup")]);
  });

  it("says so when nothing is deleted", async () => {
    serve({ "GET /v1/sessions?": () => json({ items: [], total: 0, limit: 100, offset: 0 }) });
    render(<DeletedView />, root);
    await settle();
    expect(root.textContent).toContain("Nothing deleted");
  });

  it("keeps the row and says so when a restore fails", async () => {
    serve({
      "GET /v1/sessions?": () => json({ items: [summary()], total: 1, limit: 100, offset: 0 }),
      "POST /v1/sessions/s1/restore": () => json({ detail: "gone" }, 404),
    });
    render(<DeletedView />, root);
    await settle();
    act(() => byText("Restore")!.click());
    await settle();
    expect(root.querySelectorAll("[data-testid=deleted-row]").length).toBe(1);
    expect(root.textContent).toContain("Couldn't restore");
  });
});

describe("DeleteSession", () => {
  it("asks first, and Cancel deletes nothing", async () => {
    serve({});
    render(<DeleteSession detail={detail({ deleted_at: null })} onDeleted={() => undefined} />, root);
    act(() => byText("Delete")!.click());
    expect(document.body.textContent).toContain("Recently deleted");
    act(() => byText("Cancel")!.click());
    await settle();
    expect(calls).toEqual([]);
  });

  it("deletes on confirm and tells the page", async () => {
    serve({ "DELETE /v1/sessions/s1": () => new Response(null, { status: 204 }) });
    const onDeleted = vi.fn();
    render(<DeleteSession detail={detail({ deleted_at: null })} onDeleted={onDeleted} />, root);
    act(() => byText("Delete")!.click());
    act(() => byText("Delete Session")!.click());
    await settle();
    expect(calls).toEqual(["DELETE /v1/sessions/s1"]);
    expect(onDeleted).toHaveBeenCalled();
  });

  it("is not offered while the session is still recording", () => {
    render(<DeleteSession detail={detail({ status: "live", deleted_at: null })} onDeleted={() => undefined} />, root);
    expect(byText("Delete")).toBeUndefined();
  });
});

describe("DeleteAfter", () => {
  it("sets a number of days and cancels it again", async () => {
    serve({ "PATCH /v1/sessions/s1": () => json(summary({ deleted_at: null })) });
    const onChanged = vi.fn();
    render(<DeleteAfter detail={detail({ deleted_at: null })} onChanged={onChanged} />, root);
    const select = root.querySelector("select")!;
    act(() => {
      select.value = "7";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await settle();
    expect(bodies[0]).toEqual({ delete_after_days: 7 });
    act(() => {
      select.value = "";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await settle();
    expect(bodies[1]).toEqual({ delete_after_days: null });
    expect(onChanged).toHaveBeenCalledTimes(2);
  });

  it("shows the date a pending delete-after will fire", () => {
    render(
      <DeleteAfter detail={detail({ deleted_at: null, purge_after: "2026-11-05T10:00:00Z" })} onChanged={() => undefined} />,
      root,
    );
    expect(root.textContent).toContain("Deletes");
  });
});

describe("DeletedNotice", () => {
  it("offers Restore on a deleted session and calls back with the restored one", async () => {
    serve({ "POST /v1/sessions/s1/restore": () => json(summary({ deleted_at: null })) });
    const onRestored = vi.fn();
    render(<DeletedNotice detail={detail()} onRestored={onRestored} />, root);
    expect(root.textContent).toContain("Recently deleted");
    act(() => byText("Restore")!.click());
    await settle();
    expect(onRestored).toHaveBeenCalledWith(expect.objectContaining({ deleted_at: null }));
  });

  it("renders nothing for a session that is not deleted", () => {
    render(<DeletedNotice detail={detail({ deleted_at: null })} onRestored={() => undefined} />, root);
    expect(root.textContent).toBe("");
  });
});
