import { expect, test, type Page } from "@playwright/test";

/**
 * CF-V0-E3-01 — registering a feed, in a browser.
 *
 * Step 0 of the five-step onboarding wizard had a route (`POST /api/feeds`)
 * and a Python contract suite behind it, and NO page. The only way in was a
 * terminal, and the two screens that promised one ("Register one first" on
 * the deliver door, the empty-state text on Data Intake) both linked to a
 * page with no form on it. This is what closes that gap, and proves it the
 * same way `wave1-intake.spec.ts` proves delivery: a browser, not a route
 * test that never renders.
 */

async function signIn(page: Page, who = "dev-engineer@cinqcare.test") {
  await page.goto("/signin");
  await page.locator(`[data-signin="${who}"]`).click();
  await page.waitForURL("/");
}

/** Unique per run, so a feed_id from an earlier run cannot collide. */
function feedId(): string {
  return `test-feed-${Date.now()}`;
}

test("Data Intake offers a way to register a feed", async ({ page }) => {
  await signIn(page);
  await page.goto("/data/intake");
  await expect(page.getByRole("link", { name: /register a new feed/i })).toBeVisible();
});

test("the registration form renders rather than erroring", async ({ page }) => {
  await signIn(page);
  const response = await page.goto("/data/intake/new");
  expect(response?.status()).toBeLessThan(400);
  await expect(page.getByRole("heading", { name: /register a new feed/i })).toBeVisible();
  await expect(page.locator('input[name="feed_id"]')).toBeVisible();
});

test("the form asks for the six fields and a real sample, nothing it does not need", async ({
  page,
}) => {
  await signIn(page);
  await page.goto("/data/intake/new");
  for (const name of [
    "feed_id",
    "domain",
    "source_system",
    "file_format",
    "landing_path",
    "schedule_cron",
    "file_pattern",
    "sample_filename",
  ]) {
    await expect(page.locator(`[name="${name}"]`)).toBeAttached();
  }
});

test("a matching pattern and sample register a new Draft feed", async ({ page }) => {
  const id = feedId();
  await signIn(page);
  await page.goto("/data/intake/new");
  await page.locator('input[name="feed_id"]').fill(id);
  await page.locator('input[name="domain"]').fill("membership");
  await page.locator('input[name="source_system"]').fill("fidelis");
  await page.locator('select[name="file_format"]').selectOption("csv");
  await page.locator('input[name="landing_path"]').fill(`landing/fidelis/${id}`);
  await page.locator('input[name="schedule_cron"]').fill("0 6 * * 1");
  await page.locator('input[name="file_pattern"]').fill(`^${id}_\\d{8}\\.csv$`);
  await page.locator('input[name="sample_filename"]').fill(`${id}_20260830.csv`);
  await expect(page.locator('[data-verdict="match"]')).toBeVisible();

  await page.getByRole("button", { name: /register feed/i }).click();
  await page.waitForURL(/outcome=/);
  await expect(page.locator('[data-outcome="CREATED"]')).toBeVisible();
  await expect(page.getByText(new RegExp(`${id} is saved as a Draft`))).toBeVisible();
  await expect(page.getByRole("link", { name: new RegExp(`open ${id}`, "i") })).toBeVisible();

  // Round-trips for real: the SAME feed reachable through the SAME route
  // every other feed uses, not a fixture only this screen knows about.
  await page.goto(`/data/intake/feed/${id}`);
  await expect(page.getByRole("heading", { level: 1, name: id })).toBeVisible();
  await expect(page.getByText(/draft/i).first()).toBeVisible();
});

test("a newly registered feed is immediately deliverable to", async ({ page }) => {
  const id = feedId();
  await signIn(page);
  await page.goto("/data/intake/new");
  await page.locator('input[name="feed_id"]').fill(id);
  await page.locator('input[name="domain"]').fill("claims");
  await page.locator('input[name="source_system"]').fill("uhc");
  await page.locator('select[name="file_format"]').selectOption("xlsx");
  await page.locator('input[name="landing_path"]').fill(`landing/uhc/${id}`);
  await page.locator('input[name="schedule_cron"]').fill("0 7 * * *");
  await page.locator('input[name="file_pattern"]').fill(`^${id}\\.xlsx$`);
  await page.locator('input[name="sample_filename"]').fill(`${id}.xlsx`);
  await page.getByRole("button", { name: /register feed/i }).click();
  await page.waitForURL(/outcome=/);

  await page.getByRole("link", { name: new RegExp(`upload its first sample`, "i") }).click();
  await page.waitForURL(new RegExp(`/data/intake/feed/${id}/deliver$`));
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();

  // And the general door's dropdown offers it too — the same list every
  // other feed appears in, built from the same `/api/feeds`.
  await page.goto("/data/intake/deliver");
  await expect(page.locator(`option[value="${id}"]`)).toHaveCount(1);
});

test("the browser reads the pattern as advice and never as a gate", async ({ page }) => {
  // The same rule `DeliverForm`'s check follows: the platform's refusal is
  // the honest one, computed server-side. A browser that blocked submission
  // on its own guess would be a second judge of a question only the server
  // may answer — and this one would be wrong far more often, since the
  // browser's RegExp dialect is not Python's.
  const id = feedId();
  await signIn(page);
  await page.goto("/data/intake/new");
  await page.locator('input[name="feed_id"]').fill(id);
  await page.locator('input[name="domain"]').fill("membership");
  await page.locator('input[name="source_system"]').fill("fidelis");
  await page.locator('select[name="file_format"]').selectOption("csv");
  await page.locator('input[name="landing_path"]').fill(`landing/fidelis/${id}`);
  await page.locator('input[name="schedule_cron"]').fill("0 6 * * 1");
  await page.locator('input[name="file_pattern"]').fill(`^${id}_\\d{8}\\.csv$`);
  await page.locator('input[name="sample_filename"]').fill("this_name_matches_nothing.csv");
  await expect(page.locator('[data-verdict="mismatch"]')).toBeVisible();
  await expect(page.getByRole("button", { name: /register feed/i })).toBeEnabled();

  await page.getByRole("button", { name: /register feed/i }).click();
  await page.waitForURL(/outcome=/);
  await expect(page.locator('[data-outcome="REFUSED"]')).toBeVisible();
  await expect(page.getByText(/does not match|pattern/i).first()).toBeVisible();
});

test("an empty form is refused by the server, not silently accepted", async ({ page }) => {
  await signIn(page);
  await page.goto("/data/intake/new");
  await page.locator("form").evaluate((form: HTMLFormElement) => (form.noValidate = true));
  await page.getByRole("button", { name: /register feed/i }).click();
  await page.waitForURL(/outcome=/);
  await expect(page.locator('[data-outcome="REFUSED"]')).toBeVisible();
});

test("a reader is told they may not register, rather than handed a button that 403s", async ({
  page,
}) => {
  // Registering is CREATE_FEED — a reader who could add to the registry could
  // put a feed into the estate nobody reviewed. The route refuses at the
  // server; this asserts the screen says so first, in a sentence.
  await signIn(page, "dev-analyst@cinqcare.test");
  const response = await page.goto("/data/intake/new");
  expect(response?.status()).toBeLessThan(500);
  await expect(page.getByRole("button", { name: /register feed/i })).toBeDisabled();
  await expect(page.getByText(/create_feed/)).toBeVisible();
});
