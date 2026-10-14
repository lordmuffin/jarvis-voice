import { expect, test, type Page } from "@playwright/test";
import { BACKLOG, DRAFTS, FRESH, LIVE_ID, mockLive } from "./live-fixture";

// The B · Feed live view, driven by a scripted session (see live-fixture.ts).

async function signInAsRoot(page: Page): Promise<string> {
  await page.goto("/");
  await page.getByRole("button", { name: "Sign In with Pocket-ID" }).click();
  await page.getByRole("link", { name: "root", exact: true }).click();
  await expect(page.getByTestId("user-name")).toBeVisible();
  const me = await page.request.get("/v1/me");
  return ((await me.json()) as { id: string }).id;
}

/** Open the fixture session with the backlog delivered, and time moved past the "already there" window. */
async function openLive(page: Page, opts: { mine?: boolean } = {}) {
  await page.clock.install();
  const ownerId = await signInAsRoot(page);
  const live = await mockLive(page, { ownerId, mine: opts.mine });
  await page.goto(`/sessions/${LIVE_ID}`);
  await live.waitForSocket(1);
  live.send(...BACKLOG, ...DRAFTS);
  await expect(page.getByTestId("segment")).toHaveCount(10);
  await page.clock.fastForward(4000); // what is on screen now was already there
  return live;
}

const feed = (page: Page) => page.getByRole("log", { name: "Transcript and copilot" });
const atBottom = (page: Page) =>
  feed(page).evaluate((el) => el.scrollHeight - el.scrollTop - el.clientHeight <= 24);

test("the feed is one timeline: speech, a marker, findings where they were found, drafts last", async ({ page }) => {
  await openLive(page);
  const rows = feed(page).locator("> *");
  const texts = await rows.allInnerTexts();
  const at = (needle: string) => texts.findIndex((t) => t.includes(needle));
  expect(at("Pricing")).toBeGreaterThan(at("GPU runners are a separate SKU"));
  expect(at("Pricing")).toBeLessThan(at("Send me the GPU runner pricing"));
  // A finding sits after the line that was latest when the copilot found it, not at its source time.
  expect(at("No date yet for the ARM images.")).toBeGreaterThan(at("read access to the cluster metrics"));
  // Drafts are last, outlined and italic, labelled for screen readers.
  expect(await rows.last().getAttribute("data-testid")).toBe("draft");
  await expect(page.getByTestId("draft")).toHaveCount(2);
  await expect(page.getByTestId("draft").first().locator(".i-sr")).toHaveText("(draft)");
});

test("Me and Them are told apart by a text label, not colour alone", async ({ page }) => {
  await openLive(page);
  await expect(feed(page).getByTestId("segment").first().locator(".i-spk")).toHaveText("Them");
  await expect(feed(page).getByTestId("segment").nth(1).locator(".i-spk")).toHaveText("Me");
});

test("the dock sits under the feed, never inside it, and stays put as lines arrive", async ({ page }) => {
  const live = await openLive(page);
  const dock = page.getByRole("region", { name: "Now" });
  await expect(dock).toBeVisible();
  await expect(dock).toContainText("You haven't discussed egress costs.");
  const before = await dock.boundingBox();
  live.send({ type: "segment", id: "s11", channel: "mic", speaker: "me", start_ms: 1_122_000, end_ms: 1_126_000, text: "One more line.", stt_tier: "pve" });
  await expect(feed(page).getByTestId("segment")).toHaveCount(11);
  const after = await dock.boundingBox();
  expect(after!.y).toBeCloseTo(before!.y, 0);
  expect(await feed(page).locator("[role=region][aria-label=Now]").count()).toBe(0);
});

test("a draft becomes its final in place: same row, same slot, text swapped", async ({ page }) => {
  const live = await openLive(page);
  // Pause follow so growth can't scroll the row.
  await feed(page).evaluate((el) => (el.scrollTop = 0));
  await expect(page.getByTestId("follow-state")).toHaveText("Follow paused");
  const row = page.locator("[data-key='draft-mic-1111000']");
  const handle = await row.elementHandle();
  const top = (await row.boundingBox())!.y;
  const orderBefore = await feed(page).locator("[data-line]").evaluateAll((els) => els.map((e) => e.getAttribute("data-key")));

  live.send({
    type: "segment",
    id: "s12",
    channel: "mic",
    speaker: "me",
    start_ms: 1_112_000,
    end_ms: 1_117_000,
    text: "Metrics are fine, but nothing with write access to the cluster.",
    stt_tier: "pve",
  });
  await expect(row).toHaveAttribute("data-testid", "segment");
  await expect(row.locator(".x")).toContainText("write access to the cluster.");
  expect(await row.elementHandle().then((h) => h!.evaluate((a, b) => a === b, handle))).toBe(true);
  expect((await row.boundingBox())!.y).toBeCloseTo(top, 0);
  const orderAfter = await feed(page).locator("[data-line]").evaluateAll((els) => els.map((e) => e.getAttribute("data-key")));
  expect(orderAfter).toEqual(orderBefore);
  await expect(page.getByTestId("draft")).toHaveCount(1);
});

test("follow: stays at the bottom, pauses when you scroll up, a pill counts what's new and jumps back", async ({ page }) => {
  const live = await openLive(page);
  const flood = (from: number, n: number) =>
    Array.from({ length: n }, (_, i) => ({
      type: "segment",
      id: `f${from + i}`,
      channel: "system",
      speaker: "them",
      start_ms: 1_200_000 + (from + i) * 5000,
      end_ms: 1_204_000 + (from + i) * 5000,
      text: `Filler line ${from + i}.`,
      stt_tier: "pve",
    }));
  live.send(...flood(0, 40));
  await expect(page.getByText("Filler line 39.")).toBeVisible();
  expect(await atBottom(page)).toBe(true);
  await expect(page.getByTestId("follow-state")).toHaveText("↓ Following live");
  await expect(page.getByRole("button", { name: /Jump to latest/ })).toHaveCount(0);

  // Scrolling the log up pauses follow.
  await feed(page).evaluate((el) => (el.scrollTop = el.scrollHeight / 2));
  await expect(page.getByTestId("follow-state")).toHaveText("Follow paused");
  live.send(...flood(40, 3));
  const pill = page.getByRole("button", { name: "↓ Jump to latest · 3 new" });
  await expect(pill).toBeVisible();
  expect(await atBottom(page)).toBe(false); // new lines did not yank the reader down

  await pill.click();
  await expect.poll(() => atBottom(page)).toBe(true);
  await expect(page.getByTestId("follow-state")).toHaveText("↓ Following live");
  await expect(pill).toHaveCount(0);

  // New lines keep it pinned again.
  live.send(...flood(43, 5));
  await expect(page.getByText("Filler line 47.")).toBeVisible();
  expect(await atBottom(page)).toBe(true);
});

test("new since you looked: one rule, tinted rows, no animation, cleared on your next look", async ({ page }) => {
  const live = await openLive(page);
  await expect(page.getByTestId("new-rule")).toHaveCount(0);
  live.send(FRESH);
  const rule = page.getByTestId("new-rule");
  await expect(rule).toContainText("2 new since you looked at");
  const fresh = feed(page).locator(".i-fresh");
  await expect(fresh).toHaveCount(2);
  await expect(fresh.first()).toContainText("Send the node inventory.");
  await expect(fresh.first()).toContainText("New");
  // Static tint: nothing in the feed animates.
  const animated = await feed(page).evaluate((el) =>
    [...el.querySelectorAll("*")].filter((e) => getComputedStyle(e).animationName !== "none").length,
  );
  expect(animated).toBe(0);

  // Your next real look (a click, then 2 s of attention) moves the rule.
  await page.getByRole("region", { name: "Now" }).click({ position: { x: 5, y: 5 } });
  await page.clock.fastForward(2100);
  await expect(page.getByTestId("new-rule")).toHaveCount(0);
  await expect(feed(page).locator(".i-fresh")).toHaveCount(0);
});

test("the filter groups findings in the design's order, with counts", async ({ page }) => {
  const live = await openLive(page);
  live.send(FRESH);
  const show = page.getByRole("radiogroup", { name: "Show" });
  await expect(show.getByRole("radio", { name: "Actions 2" })).toBeVisible();
  await expect(show.getByRole("radio", { name: "Decisions 1" })).toBeVisible();
  await expect(show.getByRole("radio", { name: "Notes 1" })).toBeVisible();
  await expect(show.getByRole("radio", { name: "Related 1" })).toBeVisible();
  await expect(show.getByRole("radio", { name: "Work 1" })).toBeVisible();
  await show.getByRole("radio", { name: "Actions 2" }).click();
  await expect(feed(page).getByTestId("copilot-action")).toHaveCount(2);
  await expect(feed(page).getByTestId("segment")).toHaveCount(0);
  await show.getByRole("radio", { name: "All" }).click();
  await expect(feed(page).getByTestId("segment").first()).toBeVisible();
});

test("dismissing a suggestion is remembered for this session, and can be undone", async ({ page }) => {
  const live = await openLive(page);
  live.send(FRESH);
  const dock = page.getByRole("region", { name: "Now" });
  await expect(dock).toContainText("Ask whether ARM images are on the public roadmap.");
  await expect(dock.getByTestId("suggestion")).toHaveCount(2);

  await dock.getByRole("button", { name: "Dismiss suggestion" }).first().click();
  // The next suggestion takes the lead; the dismissed one waits below with an Undo.
  await expect(dock.getByTestId("suggestion").first()).toContainText("You haven't discussed egress costs.");
  await expect(dock.locator("[data-state=dismissed]")).toContainText("Dismissed");
  expect(await page.evaluate((id) => localStorage.getItem(`jarvis-live:dismissed:${id}`), LIVE_ID)).toContain("q1");

  // After a reload the dismissed one stays gone.
  await page.reload();
  await live.waitForSocket(2);
  live.send(...BACKLOG, FRESH);
  await expect(page.getByTestId("segment")).toHaveCount(10);
  await expect(page.getByRole("region", { name: "Now" })).toContainText("You haven't discussed egress costs.");
  await expect(page.getByRole("region", { name: "Now" })).not.toContainText("Ask whether ARM images");
});

test("Undo brings a dismissed suggestion back", async ({ page }) => {
  const live = await openLive(page);
  live.send(FRESH);
  const dock = page.getByRole("region", { name: "Now" });
  await dock.getByRole("button", { name: "Dismiss suggestion" }).first().click();
  await dock.getByRole("button", { name: /Undo/ }).click();
  await expect(dock).toContainText("Ask whether ARM images are on the public roadmap.");
  expect(await page.evaluate((id) => localStorage.getItem(`jarvis-live:dismissed:${id}`), LIVE_ID)).not.toContain("q1");
});

test("End Session asks first, starts on Keep Recording, and is the owner's alone", async ({ page }) => {
  const live = await openLive(page);
  await page.getByRole("button", { name: "End Session" }).click();
  const dialog = page.getByRole("dialog", { name: "End this session?" });
  await expect(dialog).toContainText("Recording stops on the Mac. Jarvis writes the note from the transcript.");
  await expect(dialog.getByRole("button", { name: "Keep Recording" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  expect(live.ended()).toBe(0);

  await page.getByRole("button", { name: "End Session" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "End Session" }).click();
  await expect.poll(() => live.ended()).toBe(1);
  await expect(page.getByTestId("status-chip").first()).toContainText("Finalizing");
});

test("someone else's session has no End Session", async ({ page }) => {
  await openLive(page, { mine: false });
  await expect(page.getByRole("button", { name: "End Session" })).toHaveCount(0);
  await expect(page.getByTestId("owner")).toContainText("Sam");
});

test("the header shows status, elapsed and the link", async ({ page }) => {
  await openLive(page);
  const head = page.getByTestId("session-head");
  await expect(head).toContainText("Meeting");
  await expect(head.getByTestId("status-chip")).toContainText("Live");
  await expect(head.getByTestId("elapsed")).toHaveText("18:41");
  await expect(head.getByTestId("connection")).toHaveText("Connected");
  await expect(head).toContainText("pve · lag 840 ms");
});

test("work items show among the findings and under the dock", async ({ page }) => {
  await openLive(page);
  await expect(feed(page).getByTestId("feed-work")).toContainText("Add ARM runner scale set to k3s2");
  await expect(page.getByTestId("work-items")).toContainText("Add ARM runner scale set to k3s2");
});
