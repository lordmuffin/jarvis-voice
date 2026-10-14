import { execFileSync, spawn } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

// Fixed by server/tests/e2e_server.py: alice's device token, and the fake Pocket-ID's users
// (alice, bob, root = admin, mallory).
const TOKEN = "e2e-device-token";
const SERVER_DIR = join(dirname(fileURLToPath(import.meta.url)), "../../server");
const BASE = "http://127.0.0.1:8765";
const asAlice = { headers: { Authorization: `Bearer ${TOKEN}` } };

/** Sign in through the fake Pocket-ID, starting at `path`; it sends the browser back there. */
async function signIn(page: Page, user: string, path = "/"): Promise<void> {
  await page.goto(path);
  await page.getByRole("button", { name: "Sign In with Pocket-ID" }).click();
  await expect(page).toHaveTitle("Fake Pocket-ID");
  await page.getByRole("link", { name: user, exact: true }).click();
  await expect(page.getByTestId("user-name")).toBeVisible();
  await expect(page).toHaveURL(new URL(path, BASE).href);
}

/** The id of alice's newest finished session (the first test creates one). */
async function aliceSessionId(request: APIRequestContext): Promise<string | undefined> {
  const res = await request.get("/v1/sessions?limit=20", asAlice);
  const page = (await res.json()) as { items: { id: string; status: string }[] };
  return page.items.find((s) => s.status === "done")?.id;
}

async function liveSessionId(request: APIRequestContext, known: Set<string>): Promise<string> {
  let id: string | undefined;
  await expect
    .poll(
      async () => {
        const res = await request.get("/v1/sessions?limit=20", asAlice);
        const page = (await res.json()) as { items: { id: string; status: string }[] };
        id = page.items.find((s) => s.status === "live" && !known.has(s.id))?.id;
        return id;
      },
      { timeout: 20_000 },
    )
    .toBeTruthy();
  return id!;
}

test("replayed audio shows up live, then as a past session", async ({ page, request }) => {
  const dir = mkdtempSync(join(tmpdir(), "jarvis-web-e2e-"));
  const wav = join(dir, "talk.wav");
  execFileSync("uv", ["run", "python", "-m", "tests.e2e_server", "wav", wav], { cwd: SERVER_DIR });

  // Signed out: the sign-in screen, with no token field and nothing in localStorage.
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Jarvis Live" })).toBeVisible();
  await expect(page.getByLabel("Device token")).toHaveCount(0);
  await signIn(page, "alice");
  await expect(page.getByRole("heading", { name: "Sessions" })).toBeVisible();
  await expect(page.getByTestId("user-name")).toHaveText("Alice Example");
  await expect(page.getByRole("link", { name: "Admin" })).toHaveCount(0);
  expect(await page.evaluate(() => localStorage.length)).toBe(0);

  // Stream a WAV as alice through the real replay CLI, slowly enough to watch it arrive.
  const before = new Set<string>();
  const replay = spawn(
    "uv",
    ["run", "jarvis-live", "replay", "--server", BASE, "--token", TOKEN, "--wav", wav, "--speed", "1"],
    { cwd: SERVER_DIR, stdio: "inherit" },
  );
  const replayDone = new Promise<number | null>((resolve) => replay.on("close", resolve));

  const id = await liveSessionId(request, before);
  await page.goto(`/sessions/${id}`);

  // Live view: transcript segments and a copilot note arrive over the WebSocket.
  await expect(page.getByTestId("connection")).toHaveText("Live");
  await expect(page.getByTestId("segment").first()).toContainText("seg-1");
  await expect(page.getByTestId("copilot-note").first()).toContainText("Kickoff covered");
  await expect(page.getByTestId("copilot-action").first()).toContainText("Send the deck");
  await expect(page.getByTestId("copilot-decision").first()).toContainText("Ship on Friday");
  await expect(page.getByTestId("suggestion").first()).toContainText("Who owns QA?");
  // The copilot's first cycle names the untitled session; the page picks it up by polling.
  await expect(page.getByTestId("session-title")).toHaveText("Standup kickoff");
  // Her own session: no owner label.
  await expect(page.getByTestId("owner")).toHaveCount(0);

  // When the session is finalized the page flips to the past view with the rendered note.
  expect(await replayDone).toBe(0);
  await expect(page.getByTestId("note")).toContainText("The team kicked off", { timeout: 30_000 });
  await expect(page.getByRole("heading", { name: "E2E standup" }).first()).toBeVisible();
  await expect(page.getByTestId("status-chip").first()).toHaveText(/Done/);
  await expect(page.getByTestId("segment").first()).toContainText("seg-1"); // transcript pane
  // The copilot's name is kept over the note's title (the note renders its own heading).
  const sessionTitle = page.getByTestId("session-title");
  await expect(sessionTitle).toHaveText("Standup kickoff");

  // The recording plays from the server: one player for the mic channel, seekable.
  const recording = page.getByTestId("recording");
  await expect(recording).toHaveCount(1);
  await expect(recording).toContainText("Me");
  const audio = recording.locator("audio");
  await expect(audio).toHaveAttribute("src", `/v1/sessions/${id}/audio/mic.wav`);
  const wavRes = await page.request.get(`/v1/sessions/${id}/audio/mic.wav`, {
    headers: { Range: "bytes=0-43" },
  });
  expect(wavRes.status()).toBe(206);
  expect((await wavRes.body()).subarray(0, 4).toString()).toBe("RIFF");

  // Rename inline; Escape cancels, Enter saves.
  await page.getByRole("button", { name: "Rename session" }).click();
  await page.getByLabel("Session title").fill("Not this");
  await page.getByLabel("Session title").press("Escape");
  await expect(sessionTitle).toHaveText("Standup kickoff");
  await page.getByRole("button", { name: "Rename session" }).click();
  await page.getByLabel("Session title").fill("Friday ship plan");
  await page.getByLabel("Session title").press("Enter");
  await expect(sessionTitle).toHaveText("Friday ship plan");
  await page.reload();
  await expect(sessionTitle).toHaveText("Friday ship plan");

  // The list shows it, searchable by title.
  await page.getByRole("link", { name: "← Sessions" }).click();
  await expect(page.getByTestId("session-row").first()).toBeVisible();
  await page.getByLabel("Search sessions by title").fill("zzz-no-such-title");
  await expect(page.getByTestId("session-row")).toHaveCount(0);
  await page.getByLabel("Search sessions by title").fill("ship plan");
  await expect(page.getByTestId("session-row").first()).toContainText("Friday ship plan");

  // Sign out: back to the sign-in screen, and it sticks across a reload.
  await page.getByRole("button", { name: "Sign Out" }).click();
  await expect(page.getByRole("button", { name: "Sign In with Pocket-ID" })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("button", { name: "Sign In with Pocket-ID" })).toBeVisible();
});

test("layout: two panes when wide, tabs when narrow", async ({ page, request }) => {
  const id = await aliceSessionId(request);
  test.skip(!id, "needs a session from the first test");

  await page.setViewportSize({ width: 1000, height: 800 });
  await signIn(page, "alice", `/sessions/${id}`);
  await expect(page.getByRole("tablist")).toHaveCount(0);
  await expect(page.getByRole("region", { name: "Transcript" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Note" })).toBeVisible();

  await page.setViewportSize({ width: 400, height: 800 });
  await expect(page.getByRole("tablist")).toBeVisible();
  await expect(page.getByRole("tab")).toHaveCount(2);
  await page.getByRole("tab", { name: "Transcript" }).click();
  await expect(page.getByTestId("segment").first()).toBeVisible();
});

test("users see only their own sessions", async ({ page, request }) => {
  const id = await aliceSessionId(request);
  test.skip(!id, "needs a session from the first test");

  await signIn(page, "bob");
  await expect(page.getByTestId("user-name")).toHaveText("Bob Example");
  await expect(page.getByRole("heading", { name: "Sessions" })).toBeVisible();
  await expect(page.getByText("Loading…")).toHaveCount(0);
  await expect(page.locator(`a[href="/sessions/${id}"]`)).toHaveCount(0);
  // Not an admin: no Admin link, no All users toggle.
  await expect(page.getByRole("link", { name: "Admin" })).toHaveCount(0);
  await expect(page.getByLabel("All users")).toHaveCount(0);

  // Alice's session doesn't exist as far as bob can tell, recording included.
  await page.goto(`/sessions/${id}`);
  await expect(page.getByRole("alert")).toHaveText("Session not found.");
  expect((await page.request.get(`/v1/sessions/${id}/audio/mic.wav`)).status()).toBe(404);
});

test("devices: add one, see its token once, revoke it", async ({ page, request }) => {
  await signIn(page, "alice", "/devices"); // comes back to the page it started on
  await expect(page.getByRole("heading", { name: "Devices", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Devices" })).toHaveAttribute("aria-current", "page");

  const name = `Mac ${Date.now()}`;
  await page.getByLabel("New device").fill(name);
  await page.getByRole("button", { name: "Add Device" }).click();
  const card = page.getByTestId("new-token");
  await expect(card).toContainText("Mac app");
  const token = await card.getByLabel("Device token").inputValue();
  expect(token.length).toBeGreaterThan(20);
  const row = page.getByTestId("device-row").filter({ hasText: name });
  await expect(row).toHaveCount(1);

  // The token works as alice's device.
  const auth = { headers: { Authorization: `Bearer ${token}` } };
  const who = await request.get("/v1/me", auth);
  expect(who.status()).toBe(200);
  expect(await who.json()).toMatchObject({ display_name: "Alice Example" });

  // Shown once: gone after Done, and not in the list after a reload.
  await card.getByRole("button", { name: "Done" }).click();
  await expect(card).toHaveCount(0);
  await page.reload();
  await expect(row).toHaveCount(1);
  await expect(page.getByText(token)).toHaveCount(0);
  await expect(page.getByLabel("Device token")).toHaveCount(0);

  // Revoke, after confirming in the dialog; the token stops working.
  await row.getByRole("button", { name: `Revoke ${name}` }).click();
  const dialog = page.getByRole("dialog", { name: `Revoke “${name}”?` });
  await expect(dialog).toContainText("It stops working right away.");
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(row).not.toContainText("Revoked");
  await row.getByRole("button", { name: `Revoke ${name}` }).click();
  await page.getByRole("dialog").getByRole("button", { name: `Revoke ${name}` }).click();
  await expect(row).toContainText("Revoked");
  await expect(row.getByRole("button")).toHaveCount(0);
  expect((await request.get("/v1/me", auth)).status()).toBe(401);
});

test("admin: users table, one user's sessions, all users", async ({ page, request }) => {
  const id = await aliceSessionId(request);
  test.skip(!id, "needs a session from the first test");
  const aliceRow = page.locator(`a[href="/sessions/${id}"]`);

  await signIn(page, "root");
  await expect(page.getByTestId("user-name")).toHaveText("Root Admin");
  await page.getByRole("link", { name: "Admin" }).click();
  await expect(page.getByRole("heading", { name: "Users" })).toBeVisible();
  const alice = page.getByTestId("user-row").filter({ hasText: "alice@example.com" });
  await expect(alice).toHaveCount(1);
  const count = Number(await alice.locator("td.num").innerText());
  expect(count).toBeGreaterThan(0);

  // A user's name opens their sessions, owner named on each row.
  await alice.getByRole("link", { name: "Alice Example" }).click();
  await expect(page).toHaveURL(/\/\?user=/);
  await expect(page.getByTestId("viewing-banner")).toContainText("Viewing Alice Example's sessions.");
  await expect(aliceRow).toBeVisible();
  await expect(aliceRow.getByTestId("owner")).toHaveText(/Alice Example/);
  await page.getByRole("link", { name: "Back to your own" }).click();
  await expect(page).toHaveURL(`${BASE}/`);
  await expect(page.getByTestId("viewing-banner")).toHaveCount(0);

  // Own sessions by default; All users adds everyone's.
  await expect(page.getByText("Loading…")).toHaveCount(0);
  await expect(aliceRow).toHaveCount(0);
  await page.getByLabel("All users").check();
  await expect(page).toHaveURL(`${BASE}/?all=1`);
  await expect(page.getByTestId("viewing-banner")).toContainText("Viewing everyone's sessions.");
  await expect(aliceRow).toBeVisible();
  await page.getByLabel("All users").uncheck();
  await expect(aliceRow).toHaveCount(0);

  // An admin can open someone else's session; it names the owner.
  await page.goto(`/sessions/${id}`);
  await expect(page.getByTestId("session-head").getByTestId("owner")).toHaveText(/Alice Example/);
});

test("publishing: edit a note, open its pull request, merge it", async ({ page }) => {
  // e2e_server seeds a finished "E2E publish me" session for root (an admin) on every start.
  await signIn(page, "root", "/publishing");
  await expect(page.getByRole("heading", { name: "Publishing" })).toBeVisible();
  const ready = page
    .getByTestId("publishing-ready")
    .getByTestId("publishing-item")
    .filter({ hasText: "E2E publish me" })
    .first();
  await ready.getByRole("link", { name: "E2E publish me" }).click();

  const chip = page.getByTestId("publication-chip");
  await expect(page.getByTestId("note")).toContainText("Ready for the vault.");
  await expect(chip).toHaveText("Not published");

  // Edit the Markdown in place.
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  const field = page.getByLabel("Note Markdown");
  await field.fill((await field.inputValue()).replace("Ready for the vault.", "Edited in the browser."));
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByTestId("note")).toContainText("Edited in the browser.");

  // Publish opens a PR; Merge (confirmed) merges it.
  await page.getByRole("button", { name: "Publish", exact: true }).click();
  await expect(chip).toHaveText("PR open");
  await expect(page.getByRole("link", { name: "Review" })).toHaveAttribute("href", /\/pull\/\d+\/files$/);
  await expect(page.getByRole("button", { name: "Regenerate" })).toBeDisabled();
  await page.getByRole("button", { name: "Merge", exact: true }).click();
  const confirm = page.getByRole("dialog", { name: /^Merge PR #\d+\?$/ });
  await expect(confirm).toContainText("Merge this note into the vault's main branch?");
  await expect(confirm.getByRole("button", { name: "Cancel" })).toBeFocused();
  await confirm.getByRole("button", { name: "Merge", exact: true }).click();
  await expect(chip).toHaveText(/Merged/);

  await page.getByRole("link", { name: "Publishing", exact: true }).click();
  const recent = page
    .getByTestId("publishing-recent")
    .getByTestId("publishing-item")
    .filter({ hasText: "E2E publish me" })
    .first();
  await expect(recent.getByTestId("publication-chip")).toHaveText(/Merged/);
});

test("release channel tag: LAB on lab, LIVE on live, signed out and in", async ({ page }) => {
  // This server is live: a red LIVE tag.
  await page.goto("/");
  const liveHeading = page.getByRole("heading", { name: /Jarvis Live/ });
  await expect(liveHeading.getByTestId("release-channel")).toHaveText("Live");
  await expect(liveHeading.getByTestId("release-channel")).toHaveClass(/i-badge--live/);

  // The same app, told it is the lab server.
  await page.route("**/healthz", (route) =>
    route.fulfill({
      json: { status: "ok", environment: "lab", version: "0.0.0", commit: "abc1234" },
    }),
  );
  await page.goto("/");
  const heading = page.getByRole("heading", { name: /Jarvis Live/ });
  await expect(heading.getByTestId("release-channel")).toHaveText("Lab");
  await expect(heading.getByTestId("release-channel")).not.toHaveClass(/i-badge--live/);
  await expect(heading.getByTestId("release-channel")).toHaveClass(/i-badge--lab/);
  await expect(heading.getByTestId("release-channel")).toHaveCSS("text-transform", "uppercase");
  await signIn(page, "alice");
  const banner = page.getByRole("banner");
  await expect(banner.getByRole("link", { name: "Jarvis Live" })).toBeVisible();
  await expect(banner.getByTestId("release-channel")).toHaveText("Lab");
});

test("the server's version sits next to the user name", async ({ page }) => {
  await page.route("**/healthz", (route) =>
    route.fulfill({
      json: { status: "ok", environment: "live", version: "1.2.3", commit: "abc1234" },
    }),
  );
  await signIn(page, "alice");
  const banner = page.getByRole("banner");
  const version = banner.getByTestId("server-version");
  await expect(version).toHaveText("v1.2.3");
  await expect(version).toHaveAttribute("title", "Server version 1.2.3 (commit abc1234)");
  // Right before the name, in the top right.
  await expect(banner.locator("[data-testid=server-version] + [data-testid=user-name]")).toBeVisible();
});

test("upload a recording: it is transcribed and gets a note", async ({ page }) => {
  const dir = mkdtempSync(join(tmpdir(), "jarvis-web-e2e-"));
  const wav = join(dir, "voice memo.wav");
  execFileSync("uv", ["run", "python", "-m", "tests.e2e_server", "wav", wav], { cwd: SERVER_DIR });

  await signIn(page, "alice");
  await page.locator("input[type=file]").setInputFiles(wav);
  const form = page.getByTestId("upload-form");
  await expect(form).toContainText("voice memo.wav");
  await expect(form.getByLabel("Title")).toHaveAttribute("placeholder", "voice memo");
  await form.getByRole("radio", { name: "Meeting" }).click();
  await form.getByRole("button", { name: "Upload" }).click();

  // The new session opens; its transcript arrives, then the note.
  await expect(page).toHaveURL(/\/sessions\/[0-9a-f-]{36}$/);
  await expect(page.getByTestId("session-title")).toHaveText(/voice memo|Standup kickoff/);
  await expect(page.getByTestId("segment").first()).toContainText("seg-");
  await expect(page.getByTestId("note")).toContainText("The team kicked off", { timeout: 30_000 });
  await expect(page.getByTestId("status-chip").first()).toHaveText(/Done/);
  await expect(page.getByTestId("recording").locator("audio")).toHaveAttribute(
    "src",
    /\/audio\/mic\.wav$/,
  );
});
