import { execFileSync, spawn } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test, type APIRequestContext } from "@playwright/test";

const TOKEN = "e2e-device-token"; // fixed by server/tests/e2e_server.py
const SERVER_DIR = join(dirname(fileURLToPath(import.meta.url)), "../../server");
const BASE = "http://127.0.0.1:8765";

async function liveSessionId(request: APIRequestContext, known: Set<string>): Promise<string> {
  let id: string | undefined;
  await expect
    .poll(
      async () => {
        const res = await request.get("/v1/sessions?limit=20", {
          headers: { Authorization: `Bearer ${TOKEN}` },
        });
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

  // Sign in with a device token (kept in localStorage).
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Jarvis Live" })).toBeVisible();
  await page.getByLabel("Device token").fill("wrong-token");
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByRole("alert")).toContainText("isn't valid");
  await page.getByLabel("Device token").fill(TOKEN);
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByRole("heading", { name: "Sessions" })).toBeVisible();
  expect(await page.evaluate(() => localStorage.length)).toBeGreaterThan(0);

  // Stream a WAV through the real replay CLI, slowly enough to watch it arrive.
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
  await expect(page.getByTestId("connection")).toHaveText("Connected");
  await expect(page.getByTestId("segment").first()).toContainText("seg-1");
  await expect(page.getByTestId("copilot-note").first()).toHaveText("Kickoff covered");
  await expect(page.getByTestId("copilot-action").first()).toContainText("Send the deck");
  await expect(page.getByTestId("copilot-decision").first()).toHaveText("Ship on Friday");
  await expect(page.getByTestId("suggestion").first()).toContainText("Who owns QA?");

  // When the session is finalized the page flips to the past view with the rendered note.
  expect(await replayDone).toBe(0);
  await expect(page.getByTestId("note")).toContainText("The team kicked off", { timeout: 30_000 });
  await expect(page.getByRole("heading", { name: "E2E standup" }).first()).toBeVisible();
  await expect(page.getByTestId("status-chip").first()).toHaveText(/Done/);
  await expect(page.getByTestId("segment").first()).toContainText("seg-1"); // transcript pane

  // The list shows it, searchable by title.
  await page.getByRole("link", { name: "← Sessions" }).click();
  await expect(page.getByTestId("session-row").first()).toBeVisible();
  await page.getByLabel("Search sessions by title").fill("zzz-no-such-title");
  await expect(page.getByTestId("session-row")).toHaveCount(0);
  await page.getByLabel("Search sessions by title").fill("untitled");
  await expect(page.getByTestId("session-row").first()).toBeVisible();
});

test("layout: two panes when wide, tabs when narrow", async ({ page, request }) => {
  await page.addInitScript((t) => localStorage.setItem("jarvis-live.device-token", t), TOKEN);
  const res = await request.get("/v1/sessions?limit=1", {
    headers: { Authorization: `Bearer ${TOKEN}` },
  });
  const { items } = (await res.json()) as { items: { id: string }[] };
  test.skip(items.length === 0, "needs a session from the first test");

  await page.setViewportSize({ width: 1000, height: 800 });
  await page.goto(`/sessions/${items[0]!.id}`);
  await expect(page.getByRole("tablist")).toHaveCount(0);
  await expect(page.getByRole("region", { name: "Transcript" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Note" })).toBeVisible();

  await page.setViewportSize({ width: 400, height: 800 });
  await expect(page.getByRole("tablist")).toBeVisible();
  await expect(page.getByRole("tab")).toHaveCount(2);
  await page.getByRole("tab", { name: "Transcript" }).click();
  await expect(page.getByTestId("segment").first()).toBeVisible();
});
