import { expect, test, type Page } from "@playwright/test";

// server/tests/e2e_server.py seeds root's "E2E work items" session with three work items:
// "Retry failed Gotify notifications" (ready to approve), "Export transcripts as SRT" (no repo,
// no criteria) and "Log when Gotify retries give up". root is an admin, so the copilot mines
// root's sessions for work items. Newest session first, so .first() is this run's seed.
const BASE = "http://127.0.0.1:8765";

async function signIn(page: Page, user: string, path = "/"): Promise<void> {
  await page.goto(path);
  await page.getByRole("button", { name: "Sign In with Pocket-ID" }).click();
  await expect(page).toHaveTitle("Fake Pocket-ID");
  await page.getByRole("link", { name: user, exact: true }).click();
  await expect(page.getByTestId("user-name")).toBeVisible();
  await expect(page).toHaveURL(new URL(path, BASE).href);
}

const card = (page: Page, title: string) =>
  page.getByTestId("work-item").filter({ has: page.getByText(title, { exact: true }) }).first();

test.use({ permissions: ["clipboard-read", "clipboard-write"] });

test("review work items: edit, approve, copy, merge, discard", async ({ page }) => {
  await signIn(page, "root", "/work-items");
  await expect(page.getByRole("heading", { name: "Work Items" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Work Items", exact: true })).toBeVisible();

  // A draft with no repo or criteria says what it needs and cannot be approved yet.
  const srt = card(page, "Export transcripts as SRT");
  await expect(srt.getByTestId("work-item-problems")).toContainText("choose a repository");
  await expect(srt.getByRole("button", { name: "✓ Approve" })).toBeDisabled();

  // Edit it: pick a repo, add a criterion. Then it can be approved.
  await srt.getByRole("button", { name: "Edit" }).click();
  await srt.getByLabel("Repository").selectOption("lordmuffin/jarvis-voice");
  await srt.getByLabel("Acceptance criteria (one per line)").fill("- exports valid SRT\n");
  await srt.getByRole("button", { name: "Save" }).click();
  await expect(srt.getByTestId("work-item-problems")).toHaveCount(0);
  await expect(srt.getByTestId("work-item-criteria")).toContainText("exports valid SRT");

  // Approve the Gotify item, then copy its frozen prompt.
  const gotify = card(page, "Retry failed Gotify notifications");
  await gotify.getByRole("button", { name: "Preview Prompt" }).click();
  await expect(gotify.getByTestId("work-item-prompt")).toContainText("## First: read the repo and plan");
  await gotify.getByRole("button", { name: "✓ Approve" }).click();
  await expect(gotify).toHaveAttribute("data-state", "approved");
  await gotify.getByRole("button", { name: "⎘ Copy Prompt" }).click();
  await expect(gotify.getByRole("button", { name: "✓ Copied" })).toBeVisible();
  const clip = await page.evaluate(() => navigator.clipboard.readText());
  expect(clip).toContain("You are working in the GitHub repository **lordmuffin/jarvis-voice**.");
  expect(clip).toContain("## Task: Retry failed Gotify notifications");
  expect(clip).toContain("## Done when");

  // Run it as Claude Code: the e2e server's cluster opens a draft PR at once.
  page.once("dialog", (d) => void d.accept());
  await gotify.getByRole("button", { name: "▶ Run in Claude Code" }).click();
  const runStatus = gotify.getByTestId("dispatch");
  await expect(runStatus.getByRole("link", { name: "Draft PR #999" })).toBeVisible({ timeout: 15_000 });
  await expect(runStatus).toContainText("E2E: the change is ready for review.");
  await expect(gotify.getByRole("button", { name: "▶ Run Again" })).toBeVisible();

  // Merge the "give up" draft into the SRT draft (same session): one item, both sources.
  const giveUp = card(page, "Log when Gotify retries give up");
  await srt.getByRole("checkbox").check();
  await giveUp.getByRole("checkbox").check();
  const bar = page.getByTestId("merge-bar");
  await bar.getByRole("button", { name: "Export transcripts as SRT" }).click();
  await expect(card(page, "Log when Gotify retries give up")).toHaveCount(0); // merged away
  await expect(srt.getByTestId("work-item-sources").locator("a")).toHaveCount(2);

  // Discard the merged draft; it moves to the Discarded view, and can come back.
  page.once("dialog", (d) => void d.accept());
  await srt.getByRole("button", { name: "✕ Discard" }).click();
  await expect(card(page, "Export transcripts as SRT")).toHaveCount(0);
  await page.getByRole("tab", { name: "Discarded" }).click();
  await card(page, "Export transcripts as SRT").getByRole("button", { name: "↩ Restore" }).click();
  await page.getByRole("tab", { name: "Open" }).click();
  await expect(card(page, "Export transcripts as SRT")).toHaveAttribute("data-state", "draft");

  // A source link opens the session with that moment highlighted in the transcript.
  await gotify.getByRole("link", { name: "▶ 0:21" }).click();
  await expect(page).toHaveURL(/\/sessions\/.+#t=21000$/);
  await expect(page.getByTestId("work-items")).toBeVisible();
  await expect(page.locator(".i-utt--target")).toContainText("Gotify pushes vanish");
});
