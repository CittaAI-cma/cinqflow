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
//
// The route, the connector and the landing decision all had Python coverage
// before any of this: 74 contract tests over three adapters, 21 over the route.
// None of it proved the thing a person actually touches, and the form that
// posted to that route reported `REJECTED — Error: NEXT_REDIRECT` for every
// delivery it made, because `redirect()` throws and it was called inside the
// try/catch that renders refusals. A green API contract does not make a
// working product.

/** Unique per run, so a fingerprint from an earlier run cannot make this SKIP. */
function roster(): Buffer {
  return Buffer.from(`MemberID,First_Name\nM${Date.now()},Ada\n`);
}

async function choose(page: Page, name: string, content: Buffer) {
  // `#file` is the SAMPLE's input. CF-V1-E16-06 added a second file input to
  // this page — the payer's companion guide (`#doc-file`) — so a bare
  // `input[type="file"]` is ambiguous, and Playwright's strict mode says so
  // rather than silently picking one. These tests are about the sample.
  await page.locator("#file").setInputFiles({
    name,
    mimeType: "text/csv",
    buffer: content,
  });
}

test("Data Intake offers a way to take something in", async ({ page }) => {
  // The screen is named for intake. For the whole of Wave 1 it had no control
  // that put a file into the platform — the delivery step existed, two clicks
  // deep, on a page you could only reach if you already knew where you were
  // going.
  await signIn(page);
  await page.goto("/data/intake");
  await expect(page.getByRole("link", { name: /deliver a file/i })).toBeVisible();
});

test("each registered feed offers its own upload from the list", async ({ page }) => {
  await signIn(page);
  await page.goto("/data/intake");
  await page.getByRole("link", { name: new RegExp(`upload a file to ${FEED}`, "i") }).click();
  await page.waitForURL(new RegExp(`/data/intake/feed/${FEED}/deliver$`));
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
});

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
  await expect(page.locator("#file")).toBeVisible();
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

test("the Data Intake door asks which feed, because there the feed is the question", async ({
  page,
}) => {
  await signIn(page);
  await page.goto("/data/intake/deliver");
  await expect(page.getByRole("heading", { name: /deliver a file/i })).toBeVisible();
  await expect(page.getByLabel(/which feed/i)).toBeVisible();
  await expect(page.locator("#file")).toBeVisible();
});

test("a delivery reports the platform's decision, never the redirect", async ({ page }) => {
  // The regression. `redirect()` works by THROWING; called inside the catch
  // that renders refusals, it was caught and rendered AS one — so a file the
  // platform had received, registered and parked came back to the person as
  // `REJECTED — Error: NEXT_REDIRECT`. Every delivery through the only upload
  // surface in the product reported a false rejection.
  await signIn(page);
  await page.goto(`/data/intake/feed/${FEED}/deliver`);
  await choose(page, "payer_sent_the_wrong_thing.csv", roster());
  await page.getByRole("button", { name: /^deliver$/i }).click();
  await page.waitForURL(/outcome=/);
  await page.getByRole("heading", { level: 1 }).waitFor();

  const body = await page.locator("body").innerText();
  expect(body, "a redirect is not a landing decision").not.toContain("NEXT_REDIRECT");
  await expect(page.locator('[data-outcome="UNEXPECTED"]')).toBeVisible();
});

test("an unmatched file is reported as parked, not as lost", async ({ page }) => {
  // ADR-0011: every arriving file is registered, INCLUDING the unexpected
  // ones. A screen that said "rejected" and stopped would send somebody to ask
  // the payer to resend a file the platform is already holding.
  await signIn(page);
  await page.goto(`/data/intake/feed/${FEED}/deliver`);
  await choose(page, "nobody_registered_this.csv", roster());
  await page.getByRole("button", { name: /^deliver$/i }).click();
  await page.waitForURL(/outcome=/);
  await expect(page.getByText(/parked/i).first()).toBeVisible();
});

test("delivering the same content twice is reported as already held", async ({ page }) => {
  // Exactly-once ingestion, through the door a person uses. The second
  // delivery is not a failure and must not read as one.
  const content = roster();
  await signIn(page);
  for (const attempt of [1, 2]) {
    await page.goto(`/data/intake/feed/${FEED}/deliver`);
    await choose(page, "sent_again.csv", content);
    await page.getByRole("button", { name: /^deliver$/i }).click();
    await page.waitForURL(/outcome=/);
    if (attempt === 2) await expect(page.locator('[data-outcome="SKIPPED"]')).toBeVisible();
  }
});

test("an empty form is NOT reported as a landing decision", async ({ page }) => {
  // Nothing left the browser, so no check ran, no row exists and nothing was
  // decided. Rendering that as REJECTED would put a decision in somebody's
  // head that no registry row anywhere records.
  await signIn(page);
  await page.goto("/data/intake/deliver");
  // The file input is `required`, so the post is driven past the browser's own
  // validation the way a scripted client would.
  await page.locator("form").evaluate((form: HTMLFormElement) => form.noValidate = true);
  await page.getByRole("button", { name: /^deliver$/i }).click();
  await page.waitForURL(/outcome=/);
  await expect(page.locator('[data-outcome="NOT SENT"]')).toBeVisible();
  const body = await page.locator("body").innerText();
  expect(body).not.toContain("REJECTED");
});

test("the browser reads the pattern as advice and never as a gate", async ({ page }) => {
  // A browser that refused a file the platform had not seen would be a second
  // door — worse than the one ADR-0011 forbids, because a file refused here
  // leaves no registry row, no parked copy and no reason anybody can read.
  await signIn(page);
  await page.goto(`/data/intake/feed/${FEED}/deliver`);
  await choose(page, "definitely_not_the_pattern.csv", roster());
  await expect(page.locator('[data-verdict="mismatch"]')).toBeVisible();
  await expect(page.getByText(/deliver it anyway/i)).toBeVisible();
  await expect(page.getByRole("button", { name: /^deliver$/i })).toBeEnabled();
});

test("the browser confirms a name that DOES match, before anything is sent", async ({ page }) => {
  await signIn(page);
  await page.goto(`/data/intake/feed/${FEED}/deliver`);
  await choose(page, "_CINQDOWNSTATE_Member_Roster_20261001.xlsx", roster());
  await expect(page.locator('[data-verdict="match"]')).toBeVisible();
});

test("a past delivery stays reachable from the feed page, not only from the redirect", async ({
  page,
}) => {
  // The gap: `deliverFile`'s redirect carries a ONE-TIME link to the profile
  // it just made (`DeliveryOutcome`'s "What the platform read from it"). Leave
  // the page — the feed page a person actually returns to had no way back to
  // it at all, for a delivery whose facts were sitting in the database the
  // whole time. `GET /api/feeds/{id}/profiles` existed before this test did;
  // nothing on this page ever called it.
  //
  // The filename MUST match the feed's own pattern: profiling only runs for
  // an ACCEPTED delivery (`DeliveryOutcome.profile` docstring — a rejected or
  // unexpected file is never profiled, since that would produce facts about
  // bytes the platform declined to load), so an unmatched name would never
  // reach `list_profiles` regardless of anything this page renders.
  await signIn(page);
  await page.goto(`/data/intake/feed/${FEED}/deliver`);
  await choose(page, "_CINQDOWNSTATE_Member_Roster_20261225.xlsx", roster());
  await page.getByRole("button", { name: /^deliver$/i }).click();
  await page.waitForURL(/outcome=/);
  await expect(page.locator('[data-outcome="ACCEPTED"]')).toBeVisible();

  // Navigate AWAY and back, the way a person actually returns — not by
  // following the one-time redirect link this test deliberately does not use.
  await page.goto("/data/intake");
  await page.goto(`/data/intake/feed/${FEED}`);

  await expect(page.getByText(/recent deliveries/i)).toBeVisible();
  const row = page.getByRole("link", { name: "_CINQDOWNSTATE_Member_Roster_20261225.xlsx" });
  await expect(row).toBeVisible();
  await row.click();
  await page.waitForURL(/\/data\/intake\/profile\//);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
});

test("a feed with nothing delivered yet says so, not an empty table", async ({ page }) => {
  await signIn(page);
  await page.goto("/data/intake/new");
  const id = `no-deliveries-yet-${Date.now()}`;
  await page.locator('input[name="feed_id"]').fill(id);
  await page.locator('input[name="domain"]').fill("membership");
  await page.locator('input[name="source_system"]').fill("fidelis");
  await page.locator('select[name="file_format"]').selectOption("csv");
  await page.locator('input[name="landing_path"]').fill(`landing/fidelis/${id}`);
  await page.locator('input[name="schedule_cron"]').fill("0 6 * * 1");
  await page.locator('input[name="file_pattern"]').fill(`^${id}\\.csv$`);
  await page.locator('input[name="sample_filename"]').fill(`${id}.csv`);
  await page.getByRole("button", { name: /register feed/i }).click();
  await page.waitForURL(/outcome=/);

  await page.goto(`/data/intake/feed/${id}`);
  await expect(page.getByText(/nothing delivered yet/i)).toBeVisible();
});

test("a reader is told they may not deliver, rather than handed a button that 403s", async ({
  page,
}) => {
  // Delivering is EDIT_FEED — a reader who could deliver could put content
  // into the estate. The route refuses at the server; this asserts the screen
  // says so first, in a sentence, rather than rendering an inviting control.
  await signIn(page, "dev-analyst@cinqcare.test");
  const response = await page.goto(`/data/intake/feed/${FEED}/deliver`);
  expect(response?.status()).toBeLessThan(500);
  await expect(page.getByRole("button", { name: /^deliver$/i })).toBeDisabled();
  // Both forms on this page name the permission — the sample's and the
  // companion guide's — and both are correct. `.first()` asserts that a
  // reader is TOLD, which is what the test is about, rather than that they
  // are told exactly once.
  await expect(page.getByText(/edit_feed/).first()).toBeVisible();
});
