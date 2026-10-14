import { expect, test, type Page } from "@playwright/test";

// The sign-in error pages, rendered by the real server (not the dashboard). The fake Pocket-ID
// lists alice, bob, root and mallory; mallory has no group, so she is not allowed in.

/** Start a sign-in and stop on the fake Pocket-ID page; returns its URL. */
async function atIdentityProvider(page: Page): Promise<string> {
  await page.goto("/auth/login");
  await expect(page).toHaveTitle("Fake Pocket-ID");
  return page.url();
}

test("an expired or unknown link: a page, still a 400", async ({ page }) => {
  const res = await page.goto("/auth/callback?state=nope&code=x");
  expect(res!.status()).toBe(400);
  expect(res!.headers()["content-type"]).toContain("text/html");
  await expect(page.getByRole("heading", { name: "This sign-in link has expired." })).toBeVisible();
  await expect(page.getByText("Please try again.")).toBeVisible();
  await expect(page.getByText("Jarvis Live", { exact: true })).toBeVisible();
  await expect(page.locator(".i-badge")).toHaveText("Live");
  await page.getByRole("link", { name: "Sign In Again" }).click();
  await expect(page).toHaveTitle("Fake Pocket-ID");
});

test("the identity provider said no: the failed page names why, still a 400", async ({ page }) => {
  const url = await atIdentityProvider(page);
  const res = await page.goto(`${url}&user=deny`);
  expect(res!.status()).toBe(400);
  await expect(page.getByRole("heading", { name: "Sign-in failed." })).toBeVisible();
  await expect(page.getByRole("alert")).toContainText("access_denied");
  await expect(page.getByRole("link", { name: "Sign In Again" })).toHaveAttribute("href", "/auth/login");
});

test("an account that is not allowed: named, still a 403, with Sign Out and Home", async ({ page }) => {
  await atIdentityProvider(page);
  const [res] = await Promise.all([
    page.waitForResponse((r) => r.url().includes("/auth/callback")),
    page.getByRole("link", { name: "mallory", exact: true }).click(),
  ]);
  expect(res.status()).toBe(403);
  await expect(
    page.getByRole("heading", { name: "Your account (mallory@example.com) is not allowed to use Jarvis Live." }),
  ).toBeVisible();
  await expect(page.getByText("Ask an admin to add you.")).toBeVisible();
  // No session was created.
  expect((await page.request.get("/v1/me")).status()).toBe(401);

  await page.getByRole("button", { name: "Sign Out" }).click();
  await expect(page).toHaveURL("http://127.0.0.1:8765/");
  await expect(page.getByRole("button", { name: "Sign In with Pocket-ID" })).toBeVisible();
});

test("Go to Home leaves the page", async ({ page }) => {
  await atIdentityProvider(page);
  await page.getByRole("link", { name: "mallory", exact: true }).click();
  await page.getByRole("link", { name: "Go to Home" }).click();
  await expect(page.getByRole("button", { name: "Sign In with Pocket-ID" })).toBeVisible();
});

test("the page needs nothing else: no scripts, no requests beyond itself", async ({ page }) => {
  const requested: string[] = [];
  page.on("request", (r) => requested.push(new URL(r.url()).pathname));
  await page.goto("/auth/callback?state=nope&code=x");
  await expect(page.getByRole("heading", { name: "This sign-in link has expired." })).toBeVisible();
  expect(requested).toEqual(["/auth/callback"]);
  expect(await page.locator("script").count()).toBe(0);
});
