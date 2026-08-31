import { expect, test, type Page } from "@playwright/test";

/**
 * W1-35 (F6) · CF-V1-E6-02 — the acceptance rate per agent per week, in a
 * browser, reached by URL directly rather than through the sidebar.
 *
 * `agent-acceptance` is declared at `wave=1` in `core/navigation.py`, and
 * `ACTIVE_WAVE` stays 0 in this slab on purpose — the same discipline
 * `wave1-intake.spec.ts` already tests for `mapping`, `quality` and
 * `work-queue`: a destination existing in the data is not the same thing as
 * it being activated, and this route is tested straight, the way any other
 * Wave-1 screen is today.
 *
 * The numbers asserted below are `intelligence.demo._seed_agent_acceptance_
 * history`'s own four mapping-suggestion proposals (50%, 75%, 90%, 100%,
 * oldest to newest) — REAL seeded data returned by the real route, not a
 * fixture invented for this file. They are seeded a full 1-4 weeks before
 * `now`, deliberately, so they cannot land in the SAME ISO-week bucket as
 * `_seed_mapping_review`'s own mapping-suggestion proposal (`now - 2h`) —
 * once `mapping-proposal-review.spec.ts` approves that one, THIS week's
 * bucket carries a second, unrelated proposal, which is why nothing here
 * asserts about the current week or the "Latest week" / trend tiles: those
 * are correctly sensitive to whatever else this shared demo plane decided
 * today, and only the four fixed past weeks are this file's own to check.
 */

async function signIn(page: Page, who: string) {
  await page.goto("/signin");
  await page.locator(`[data-signin="${who}"]`).click();
  await page.waitForURL("/");
}

const READ_ONLY = "dev-analyst@cinqcare.test";

// The seeded history, oldest week first — `intelligence.demo._seed_agent_
// acceptance_history`'s own four proposals. `rate` also appears in the row's
// "Inferred" column (no deterministic keys are seeded), so row assertions
// below check it by COLUMN POSITION rather than by page-wide text, which
// would otherwise match the Rate tag, the Inferred figure, and — for the
// newest week — the "Latest week" tile all at once.
const WEEKLY_HISTORY = [
  { pair: "3/6", rate: "50%" },
  { pair: "6/8", rate: "75%" },
  { pair: "9/10", rate: "90%" },
  { pair: "5/5", rate: "100%" },
];

test("the acceptance page renders the seeded weekly history for mapping-suggestion", async ({
  page,
}) => {
  await signIn(page, READ_ONLY);
  const response = await page.goto("/ai/acceptance");
  expect(response?.status()).toBeLessThan(400);
  await expect(page.getByRole("heading", { name: "Agent Acceptance" })).toBeVisible();
  // Defaults to mapping-suggestion without a query parameter.
  await expect(page.getByText("Mapping suggestion", { exact: true })).toBeVisible();

  // At least the four seeded weeks, plus the header — "at least" because the
  // demo plane is shared with every other spec file, and one of them
  // (`mapping-proposal-review.spec.ts`) approves a real mapping-suggestion
  // proposal of its own into the CURRENT week's bucket, which is real
  // aggregation working correctly, not a leak this test should fight.
  expect(await page.getByRole("row").count()).toBeGreaterThanOrEqual(5);
  for (const { pair, rate } of WEEKLY_HISTORY) {
    const row = page.getByRole("row", { name: pair });
    await expect(row).toBeVisible();
    await expect(row.getByRole("cell").nth(2)).toHaveText(pair); // Accepted / Total
    await expect(row.getByRole("cell").nth(3)).toHaveText(rate); // Rate
  }

  // The summary tiles render SOMETHING real, without pinning the exact
  // "latest week" figure — see the file header for why that one is left to
  // vary with whatever else this shared demo plane decided today.
  await expect(page.getByText("Latest week")).toBeVisible();
  await expect(page.getByText("Weeks recorded")).toBeVisible();
  await expect(page.getByText(/\d+ decided proposals? total/)).toBeVisible();
});

test("the page is not hard-coded to one agent — switching shows a real empty state", async ({
  page,
}) => {
  await signIn(page, READ_ONLY);
  await page.goto("/ai/acceptance");
  await page.getByRole("link", { name: "Schema inference" }).click();
  await page.waitForURL(/agent=schema-inference/);

  // No schema-inference proposal has ever been decided in the demo plane —
  // the honest answer is the platform's own "nothing recorded" state, never
  // a fabricated row and never last agent's numbers left on screen.
  await expect(
    page.getByText("Nothing recorded for accepted proposals from Schema inference yet."),
  ).toBeVisible();
  await expect(page.getByRole("cell", { name: "3/6", exact: true })).toHaveCount(0);
});

test("an agent name nobody seeded still renders the same honest empty state", async ({
  page,
}) => {
  await signIn(page, READ_ONLY);
  const response = await page.goto("/ai/acceptance?agent=some-future-agent");
  expect(response?.status()).toBeLessThan(400);
  await expect(page.getByRole("heading", { name: "Agent Acceptance" })).toBeVisible();
  await expect(page.getByText(/Nothing recorded for accepted proposals from/)).toBeVisible();
});
