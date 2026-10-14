import { expect, test, type Page } from "@playwright/test";

// The Sessions list's filters, sort and search, against the real server. e2e_server.py seeds
// root with the two E2E sessions (now) and six older ones in every status and mode.
const BASE = "http://127.0.0.1:8765";

async function signInAsRoot(page: Page, path = "/"): Promise<void> {
  await page.goto(path);
  await page.getByRole("button", { name: "Sign In with Pocket-ID" }).click();
  await page.getByRole("link", { name: "root", exact: true }).click();
  await expect(page.getByTestId("user-name")).toBeVisible();
}

const rows = (page: Page) => page.getByTestId("session-row");
const titleOf = (page: Page, n: number) => rows(page).nth(n).locator(".ti");
const status = (page: Page) => page.getByRole("radiogroup", { name: "Status" });

test("counts the sessions and each status that needs a look", async ({ page }) => {
  await signInAsRoot(page);
  await expect(page.getByTestId("session-count")).toHaveText("Your sessions · 8 sessions");
  await expect(status(page).getByRole("radio", { name: "Live 1" })).toBeVisible();
  await expect(status(page).getByRole("radio", { name: "Finalizing 1" })).toBeVisible();
  await expect(status(page).getByRole("radio", { name: "Failed 1" })).toBeVisible();
  await expect(page.getByTestId("shown-count")).toHaveText("8 of 8 shown");
});

test("status, mode and date narrow the list on the server, and the URL says so", async ({ page }) => {
  await signInAsRoot(page);

  await status(page).getByRole("radio", { name: "Failed 1" }).click();
  await expect(rows(page)).toHaveCount(1);
  await expect(titleOf(page, 0)).toHaveText("Sleep pipeline backfill plan");
  await expect(page).toHaveURL(/[?&]status=failed/);
  // Counts ignore the status filter itself.
  await expect(status(page).getByRole("radio", { name: "Live 1" })).toBeVisible();

  await status(page).getByRole("radio", { name: "All" }).click();
  await page.getByLabel("Mode").selectOption("solo");
  await expect(rows(page)).toHaveCount(4); // E2E work items, Homelab, Garage, Sleep pipeline
  await expect(page).toHaveURL(/mode=solo/);

  await page.getByLabel("Date").selectOption("7d");
  await expect(page.getByTestId("session-count")).toHaveText("Your sessions · 3 sessions");
  await expect(rows(page).filter({ hasText: "Homelab power budget" })).toHaveCount(0); // 9 days ago

  await page.getByRole("button", { name: "Reset Filters" }).click();
  await expect(rows(page)).toHaveCount(8);
  await expect(page).not.toHaveURL(/mode=|date=|status=/);
});

test("sort: oldest and longest first", async ({ page }) => {
  await signInAsRoot(page);
  await page.getByLabel("Sort").selectOption("oldest");
  await expect(titleOf(page, 0)).toHaveText("Old retro");
  await expect(page).toHaveURL(/sort=oldest/);
  await page.getByLabel("Sort").selectOption("longest");
  await expect(titleOf(page, 0)).toHaveText("Garage sensor walkthrough"); // still running
  await page.getByLabel("Sort").selectOption("newest");
  await expect(page).not.toHaveURL(/sort=/);
});

test("a filtered view is a link: it opens as it was left and survives a reload", async ({ page }) => {
  await signInAsRoot(page);
  await page.goto("/?status=done&mode=meeting&sort=oldest");
  await expect(status(page).getByRole("radio", { name: "Done" })).toHaveAttribute("aria-checked", "true");
  await expect(page.getByLabel("Mode")).toHaveValue("meeting");
  await expect(page.getByLabel("Sort")).toHaveValue("oldest");
  await expect(titleOf(page, 0)).toHaveText("Old retro");
  await page.reload();
  await expect(page.getByLabel("Sort")).toHaveValue("oldest");
  await expect(titleOf(page, 0)).toHaveText("Old retro");
});

test("search titles, and say so when nothing matches", async ({ page }) => {
  await signInAsRoot(page);
  await page.getByLabel("Search sessions by title").fill("backup");
  await expect(rows(page)).toHaveCount(1);
  await expect(titleOf(page, 0)).toHaveText("Offsite backup review");
  await expect(page).toHaveURL(/q=backup/);

  await page.getByLabel("Search sessions by title").fill("zzz-no-such-title");
  await expect(page.getByText("No sessions match “zzz-no-such-title”.")).toBeVisible();
  await page.getByRole("button", { name: "Clear Search" }).click();
  await expect(rows(page)).toHaveCount(8);
});

test("search transcripts: the matching line, the match in bold, and a link to it", async ({ page }) => {
  await signInAsRoot(page);
  await page.getByRole("radiogroup", { name: "Search in" }).getByRole("radio", { name: "Transcripts" }).click();
  await page.getByLabel("Search sessions by transcript").fill("pricing");
  await expect(rows(page)).toHaveCount(2);
  await expect(page).toHaveURL(/q=pricing&in=transcripts/);
  const first = rows(page).filter({ hasText: "Platform sync" });
  await expect(first.getByTestId("session-match")).toContainText("quarterly pricing review is next week");
  await expect(first.getByTestId("session-match").locator("b")).toHaveText("pricing");
  await expect(first.getByTestId("session-match")).toContainText("▶ 1:00");

  // A title-only word finds nothing in transcripts.
  await page.getByLabel("Search sessions by transcript").fill("backup review");
  await expect(page.getByText("No sessions match “backup review”.")).toBeVisible();

  await page.getByLabel("Search sessions by transcript").fill("pricing");
  await first.click();
  await expect(page).toHaveURL(/\/sessions\/[0-9a-f-]{36}#t=60000$/);
});

test("filters that match nothing say so and offer Clear Filters", async ({ page }) => {
  await signInAsRoot(page);
  await status(page).getByRole("radio", { name: "Live 1" }).click();
  await page.getByLabel("Mode").selectOption("meeting");
  await expect(page.getByText("No sessions match these filters.")).toBeVisible();
  await page.getByRole("button", { name: "Clear Filters" }).click();
  await expect(rows(page)).toHaveCount(8);
});

test("an admin's All users view keeps the filters", async ({ page }) => {
  await signInAsRoot(page);
  await status(page).getByRole("radio", { name: "Failed 1" }).click();
  await page.getByLabel("All users").check();
  await expect(page).toHaveURL(new RegExp(`${BASE}/\\?all=1&status=failed`));
  await expect(status(page).getByRole("radio", { name: "Failed" })).toHaveAttribute("aria-checked", "true");
  await expect(page.getByTestId("session-count")).toContainText("All users ·");
});

test("the footer says how long audio is kept, from the server's setting", async ({ page }) => {
  await signInAsRoot(page);
  await expect(page.getByRole("contentinfo")).toHaveText(
    "Audio is kept for 90 days. The transcript and note are kept.",
  );
});
