import { mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test, type Page } from "@playwright/test";
import { BACKLOG, DRAFTS, FRESH, LIVE_ID, PAST_ID, mockLive, mockPast } from "./live-fixture";

// Screenshots of the four main screens in both themes, for the PR and for design review. They
// are written, not compared: the fake Pocket-ID and the seeded data make them stable enough to
// look at, not to diff. Run with the rest of the suite; output goes to web/screenshots.
const OUT = join(dirname(fileURLToPath(import.meta.url)), "../screenshots");
const THEMES = ["light", "dark"] as const;
const BASE = "http://127.0.0.1:8765";

async function signIn(page: Page, user: string, path = "/"): Promise<void> {
  await page.goto(path);
  await page.getByRole("button", { name: "Sign In with Pocket-ID" }).click();
  await expect(page).toHaveTitle("Fake Pocket-ID");
  await page.getByRole("link", { name: user, exact: true }).click();
  await expect(page.getByTestId("user-name")).toBeVisible();
  await expect(page).toHaveURL(new URL(path, BASE).href);
}

async function shoot(page: Page, name: string, theme: (typeof THEMES)[number]): Promise<void> {
  mkdirSync(OUT, { recursive: true });
  await page.screenshot({ path: join(OUT, `${name}-${theme}.png`), animations: "disabled" });
}

for (const theme of THEMES) {
  test.describe(`${theme} theme`, () => {
    test.use({ colorScheme: theme, viewport: { width: 1280, height: 800 } });

    test("login", async ({ page }) => {
      await page.goto("/");
      await expect(page.getByRole("button", { name: "Sign In with Pocket-ID" })).toBeVisible();
      await expect(page.getByTestId("release-channel")).toBeVisible();
      await shoot(page, "login", theme);
    });

    test("sessions list", async ({ page }) => {
      await signIn(page, "root");
      await expect(page.getByTestId("session-row").first()).toBeVisible();
      await shoot(page, "sessions", theme);
    });

    test("sessions: filters and sort", async ({ page }) => {
      await page.setViewportSize({ width: 1280, height: 900 });
      await signIn(page, "root");
      await page.getByRole("radiogroup", { name: "Status" }).getByRole("radio", { name: "Done" }).click();
      await page.getByLabel("Mode").selectOption("meeting");
      await page.getByLabel("Date").selectOption("30d");
      await page.getByLabel("Sort").selectOption("oldest");
      await expect(page.getByTestId("session-row")).toHaveCount(2);
      await shoot(page, "sessions-filters", theme);
    });

    test("sessions: transcript search", async ({ page }) => {
      await page.setViewportSize({ width: 1280, height: 900 });
      await signIn(page, "root");
      await page.getByRole("radiogroup", { name: "Search in" }).getByRole("radio", { name: "Transcripts" }).click();
      await page.getByLabel("Search sessions by transcript").fill("pricing");
      await expect(page.getByTestId("session-match")).toHaveCount(2);
      await shoot(page, "sessions-search", theme);
    });

    test("sessions: nothing matches", async ({ page }) => {
      await page.setViewportSize({ width: 1280, height: 700 });
      await signIn(page, "root");
      await page.getByRole("radiogroup", { name: "Status" }).getByRole("radio", { name: "Live 1" }).click();
      await page.getByLabel("Mode").selectOption("meeting");
      await expect(page.getByText("No sessions match these filters.")).toBeVisible();
      await shoot(page, "sessions-empty", theme);
    });

    test("devices", async ({ page }) => {
      await signIn(page, "alice", "/devices");
      await page.getByLabel("New device").fill(`Work MacBook ${theme}`);
      await page.getByRole("button", { name: "Add Device" }).click();
      await expect(page.getByTestId("new-token")).toBeVisible();
      await page.getByTestId("new-token").getByLabel("Device token").blur();
      await shoot(page, "devices", theme);
    });

    test("live session (scripted)", async ({ page }) => {
      await page.setViewportSize({ width: 1280, height: 1040 });
      await page.clock.install();
      await signIn(page, "root");
      const me = await page.request.get("/v1/me");
      const live = await mockLive(page, { ownerId: ((await me.json()) as { id: string }).id });
      await page.goto(`/sessions/${LIVE_ID}`);
      await live.waitForSocket(1);
      live.send(...BACKLOG, ...DRAFTS);
      await expect(page.getByTestId("segment")).toHaveCount(10);
      await page.clock.fastForward(4000);
      live.send(FRESH);
      await expect(page.getByTestId("new-rule")).toBeVisible();
      await expect(page.getByRole("region", { name: "Now" })).toContainText("Ask whether ARM images");
      await shoot(page, "live", theme);

      // Scrolled up: follow paused, the pill counts what arrived since.
      await page.getByRole("log").evaluate((el) => (el.scrollTop = 0));
      await expect(page.getByTestId("follow-state")).toHaveText("Follow paused");
      live.send({ type: "segment", id: "z1", channel: "me", speaker: "me", start_ms: 1_130_000, end_ms: 1_134_000, text: "Thanks, that is everything for today.", stt_tier: "pve" });
      await expect(page.getByRole("button", { name: /Jump to latest/ })).toBeVisible();
      await shoot(page, "live-paused", theme);
    });

    test("past session: note and transcript (scripted)", async ({ page }) => {
      await page.setViewportSize({ width: 1280, height: 1000 });
      await signIn(page, "root");
      const me = await page.request.get("/v1/me");
      await mockPast(page, { ownerId: ((await me.json()) as { id: string }).id });
      await page.goto(`/sessions/${PAST_ID}`);
      await expect(page.getByTestId("note")).toBeVisible();
      await expect(page.getByTestId("segment").first()).toBeVisible();
      await shoot(page, "past", theme);
      await page.getByRole("toolbar", { name: "Note" }).getByRole("button", { name: "Edit" }).click();
      await expect(page.getByLabel("Note Markdown")).toBeVisible();
      await shoot(page, "note-editor", theme);
    });

    test("publishing", async ({ page }) => {
      await signIn(page, "root", "/publishing");
      await expect(page.getByTestId("publishing-ready")).toBeVisible();
      await shoot(page, "publishing", theme);
    });

    test("work items", async ({ page }) => {
      await signIn(page, "root", "/work-items");
      await expect(page.getByTestId("work-item").first()).toBeVisible();
      await shoot(page, "work-items", theme);
    });

    test("work items: merged", async ({ page }) => {
      await signIn(page, "root", "/work-items");
      await page.getByRole("tab", { name: /^Merged/ }).click();
      await expect(page.getByTestId("work-item").first()).toContainText("Merged into");
      await shoot(page, "work-items-merged", theme);
    });

    test("sign-in error pages (rendered by the server)", async ({ page }) => {
      await page.setViewportSize({ width: 800, height: 520 });
      await page.goto("/auth/callback?state=nope&code=x");
      await expect(page.getByRole("heading", { name: "This sign-in link has expired." })).toBeVisible();
      await shoot(page, "auth-expired", theme);

      await page.goto("/auth/login");
      await page.goto(`${page.url()}&user=deny`);
      await expect(page.getByRole("heading", { name: "Sign-in failed." })).toBeVisible();
      await shoot(page, "auth-failed", theme);

      await page.goto("/auth/login");
      await page.getByRole("link", { name: "mallory", exact: true }).click();
      await expect(page.getByText("is not allowed to use Jarvis Live.")).toBeVisible();
      await shoot(page, "auth-denied", theme);
    });

    test("admin", async ({ page }) => {
      await signIn(page, "root", "/admin");
      await expect(page.getByTestId("user-row").first()).toBeVisible();
      await shoot(page, "admin", theme);
    });
  });
}

test("the theme toggle overrides the system and survives a reload", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "light" });
  await page.goto("/");
  const bg = () => page.evaluate(() => getComputedStyle(document.querySelector(".inst")!).backgroundColor);
  const light = await bg();
  await signIn(page, "alice");
  await page.getByRole("radio", { name: "Dark" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  expect(await bg()).not.toBe(light);
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await expect(page.getByRole("radio", { name: "Dark" })).toHaveAttribute("aria-checked", "true");
  await page.getByRole("radio", { name: "System" }).click();
  await expect(page.locator("html")).not.toHaveAttribute("data-theme", /.+/);
  expect(await bg()).toBe(light);
});
