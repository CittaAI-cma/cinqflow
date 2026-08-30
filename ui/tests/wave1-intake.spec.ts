import { expect, test, type Page } from "@playwright/test";

/**
 * The Wave-1 intake surfaces, in a browser.
 *
 * Every route below is served by a route handler the Python suite covers, and
 * until now none of them was ever RENDERED by a test: the API contract was
 * green while the page that reads it had no coverage at all. Only the mapping
 * editor was reached, and only incidentally, by the Wave-0 citation deep-link
 * sweep in gap-findings.spec.ts.
 *
 * These run against the mock socket, where the seeded feed has no mapping and
 * no rules yet — which is the state a BA actually starts in, and the one where
 * a page is most likely to render a crash or a lie instead of an invitation.
 * What is asserted is that each page arrives, names its feed, and says plainly
 * that there is nothing yet rather than fabricating a mapping or a count.
 */

const FEED = "fidelis-downstate-roster";

async function signIn(page: Page, who = "dev-engineer@cinqcare.test") {
  await page.goto("/signin");
  await page.locator(`[data-signin="${who}"]`).click();
  await page.waitForURL("/");
}

const WAVE_1_ROUTES: ReadonlyArray<[label: string, route: string]> = [
  ["the mapping editor", `/data/intake/mapping/${FEED}`],
  ["the version comparison", `/data/intake/mapping/${FEED}/compare`],
  ["the rule preview", `/data/intake/rules/${FEED}`],
];

for (const [label, route] of WAVE_1_ROUTES) {
  test(`${label} renders rather than erroring`, async ({ page }) => {
    await signIn(page);
    const response = await page.goto(route);
    expect(response?.status(), `${route} must not 404`).not.toBe(404);
    expect(response?.status(), `${route} must not 500`).toBeLessThan(500);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  });
}

test("the mapping editor says there is no mapping rather than showing an empty one", async ({
  page,
}) => {
  await signIn(page);
  await page.goto(`/data/intake/mapping/${FEED}`);
  // The refusal the API actually returns is the one the page must relay. A
  // blank table here would read as "every column is mapped to nothing".
  await expect(page.getByText(/no mapping|not yet|create/i).first()).toBeVisible();
});

test("the rule preview reports no rules rather than a clean bill of health", async ({ page }) => {
  await signIn(page);
  await page.goto(`/data/intake/rules/${FEED}`);
  // The failure this forbids: zero rules rendering as "0 failed", which reads
  // as a delivery that passed every check somebody wrote.
  const body = (await page.locator("body").innerText()).toLowerCase();
  expect(body).toMatch(/no rules|not yet|write some/);
});

test("a feed page links onward to both Wave-1 surfaces", async ({ page }) => {
  await signIn(page);
  await page.goto(`/data/intake/feed/${FEED}`);
  await expect(page.getByRole("link", { name: /open the mapping/i })).toBeVisible();
  await expect(page.getByRole("link", { name: /preview the rules/i })).toBeVisible();
});

test("the mapping link from the feed page arrives at the mapping", async ({ page }) => {
  await signIn(page);
  await page.goto(`/data/intake/feed/${FEED}`);
  await page.getByRole("link", { name: /open the mapping/i }).click();
  await page.waitForURL(new RegExp(`/data/intake/mapping/${FEED}$`));
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
});

test("a read-only user may still read the mapping and the rules", async ({ page }) => {
  // Reading a mapping is VIEW, and a reviewer who cannot see what they are
  // asked to approve is being asked to approve prose — CF-V1-E7-02's reason
  // for making the preview a read-only POST.
  await signIn(page, "dev-analyst@cinqcare.test");
  for (const route of [`/data/intake/mapping/${FEED}`, `/data/intake/rules/${FEED}`]) {
    const response = await page.goto(route);
    expect(response?.status(), `${route} must not 403 a reader`).not.toBe(403);
  }
});

// ── CF-V1-E3-05 · the delivery step ─────────────────────────────────────────

test("the feed page offers the upload step the wizard names", async ({ page }) => {
  await signIn(page);
  await page.goto(`/data/intake/feed/${FEED}`);
  await expect(page.getByRole("link", { name: /upload a sample file/i })).toBeVisible();
});

test("the upload form asks for a file, a business date, and nothing it does not need", async ({
  page,
}) => {
  await signIn(page);
  await page.goto(`/data/intake/feed/${FEED}/deliver`);
  await expect(page.getByRole("heading", { name: /upload a sample file/i })).toBeVisible();
  await expect(page.locator('input[type="file"]')).toBeVisible();
  await expect(page.locator('input[name="business_date"]')).toBeVisible();
  // The pattern is SHOWN, so a rejection for not matching it is not a surprise.
  await expect(page.getByText(/file-name pattern/i)).toBeVisible();
});

test("the page says where a delivered file goes", async ({ page }) => {
  // "Where does it land" is the first question anybody asks, and an answer
  // that requires reading a profile is an answer nobody gets.
  await signIn(page);
  await page.goto(`/data/intake/feed/${FEED}/deliver`);
  await expect(page.getByText(/incoming/)).toBeVisible();
  await expect(page.getByText(/skipped/i)).toBeVisible();
});

test("a reader is not offered the upload step as if it would work", async ({ page }) => {
  // Delivering is EDIT_FEED. The route refuses at the server; what this
  // asserts is that the page still renders rather than erroring, so a reader
  // who follows a link sees an explanation instead of a stack trace.
  await signIn(page, "dev-analyst@cinqcare.test");
  const response = await page.goto(`/data/intake/feed/${FEED}/deliver`);
  expect(response?.status()).toBeLessThan(500);
});
