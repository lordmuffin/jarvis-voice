import { expect, test, type Page } from "@playwright/test";

// server/tests/e2e_server.py seeds root's "E2E work items" session with four proposed actions:
// three tasks ("Call the vendor about the quote", "Send the revised invoice", "Look into SRT
// export") and a reminder. The vault's Work Tracker is on the fake GitHub (port + 2). Reminders
// go to the owner's phone and root is not the owner, so approving it fails, visibly.
const BASE = "http://127.0.0.1:8765";
const GITHUB = "http://127.0.0.1:8767";
const TRACKER = "20 Areas/Personal/Work Tracker.md";

async function signIn(page: Page, user: string): Promise<void> {
  await page.goto("/");
  await page.getByRole("button", { name: "Sign In with Pocket-ID" }).click();
  await expect(page).toHaveTitle("Fake Pocket-ID");
  await page.getByRole("link", { name: user, exact: true }).click();
  await expect(page.getByTestId("user-name")).toBeVisible();
  await expect(page).toHaveURL(new URL("/", BASE).href);
}

const card = (page: Page, title: string) =>
  page.getByTestId("action").filter({ has: page.getByText(title, { exact: true }) }).first();

/** The Work Tracker on the fake GitHub's main branch. */
async function tracker(page: Page): Promise<string> {
  const r = await page.request.get(`${GITHUB}/repos/lordmuffin/Notes/contents/${TRACKER}?ref=main`, {
    headers: { Authorization: "Bearer fake-github-token" },
  });
  const body = (await r.json()) as { content: string };
  return Buffer.from(body.content, "base64").toString("utf8");
}

test("review proposed actions: approve, edit, reject, approve the list, retry a failure", async ({ page }) => {
  await signIn(page, "root");
  await page.getByRole("link", { name: "E2E work items" }).first().click();
  const actions = page.getByTestId("actions");
  await expect(actions.getByRole("heading", { name: "Proposed actions" })).toBeVisible();

  // Nothing has happened: four proposals, none executed, the board untouched.
  await expect(page.getByTestId("action")).toHaveCount(4);
  for (const c of await page.getByTestId("action").all()) await expect(c).toHaveAttribute("data-state", "proposed");
  expect(await tracker(page)).not.toContain("Call the vendor");

  // One tap approves a task; it lands in the Work Tracker with the session linked.
  const vendor = card(page, "Call the vendor about the quote");
  await vendor.getByRole("button", { name: "✓ Approve" }).click();
  await expect(vendor).toHaveAttribute("data-state", "succeeded", { timeout: 20_000 });
  await expect(vendor.getByTestId("action-result")).toContainText("Added to the vault");
  await expect(vendor.getByRole("link", { name: "pull request" })).toBeVisible();
  const board = await tracker(page);
  const line = board.split("\n").find((l) => l.includes("Call the vendor about the quote"))!;
  expect(line).toMatch(/^- \[ \] Call the vendor about the quote .*\[session\]\(http:\/\/127\.0\.0\.1:8765\/sessions\/[0-9a-f-]+#t=\d+\) \^jv-[0-9a-f]{8}$/);
  expect(board.indexOf(line)).toBeLessThan(board.indexOf("## Done"));
  expect(board.indexOf(line)).toBeGreaterThan(board.indexOf("## Needs Triage"));

  // Its history says who did what.
  await vendor.getByRole("button", { name: "History" }).click();
  const history = vendor.getByTestId("action-history");
  await expect(history).toContainText("Proposed by copilot:e2e");
  await expect(history).toContainText("Approved on the web");
  await expect(history).toContainText("Done");

  // Reject one; it is closed and never runs.
  const srt = card(page, "Look into SRT export");
  await srt.getByRole("button", { name: "✕ Reject" }).click();
  await expect(srt).toHaveAttribute("data-state", "rejected");
  expect(await tracker(page)).not.toContain("Look into SRT export");

  // Edit one before approving, then approve the rest of the list in one tap.
  const invoice = card(page, "Send the revised invoice");
  await invoice.getByRole("button", { name: "Edit" }).click();
  await invoice.getByLabel("Task").fill("Send the revised invoice to Dana");
  await invoice.getByRole("button", { name: "Save" }).click();
  const edited = card(page, "Send the revised invoice to Dana");
  await expect(edited).toHaveAttribute("data-state", "proposed");

  await actions.getByRole("button", { name: "✓ Approve all (2)" }).click();
  await expect(edited).toHaveAttribute("data-state", "succeeded", { timeout: 20_000 });
  expect(await tracker(page)).toContain("Send the revised invoice to Dana");

  // The reminder cannot go to root's phone: reported, not retried; Retry is the owner's call.
  const reminder = card(page, "Check the Gotify retries");
  await expect(reminder).toHaveAttribute("data-state", "failed", { timeout: 20_000 });
  await expect(reminder.getByTestId("action-result")).toContainText("Failed: reminders are pushed to the owner's phone");
  await page.waitForTimeout(1500);
  await expect(reminder).toHaveAttribute("data-state", "failed"); // nothing retried it behind our back
  await reminder.getByRole("button", { name: "↻ Retry" }).click();
  await expect(reminder.getByTestId("action-result")).toContainText("Failed: reminders are pushed");
  await reminder.getByRole("button", { name: "History" }).click();
  await expect(reminder.getByTestId("action-history")).toContainText("Retry requested on the web");

  // Approving again changes nothing: the task is on the board exactly once.
  const final = await tracker(page);
  expect(final.split("Call the vendor about the quote").length - 1).toBe(1);
});
