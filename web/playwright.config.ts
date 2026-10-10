import { defineConfig, devices } from "@playwright/test";

const PORT = 8765;

// The real app (FakeSTT/FakeLLM, real Postgres) serves the built web/dist. Run
// `npm run build` first. JARVIS_LIVE_TEST_DATABASE_URL picks a database; otherwise the
// harness starts a Postgres testcontainer (needs Docker).
export default defineConfig({
  testDir: "e2e",
  timeout: 90_000,
  workers: 1,
  fullyParallel: false,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: { baseURL: `http://127.0.0.1:${PORT}`, trace: "retain-on-failure" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: `uv run python -m tests.e2e_server serve --port ${PORT}`,
    cwd: "../server",
    url: `http://127.0.0.1:${PORT}/healthz`,
    reuseExistingServer: !process.env.CI,
    timeout: 180_000,
  },
});
