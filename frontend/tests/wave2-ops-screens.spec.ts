import { expect, test, type Page } from "@playwright/test";

/**
 * W2-33 · CF-V2-E12-03/E12-04/E13-04 — Incidents and Certification, in a
 * browser, reached by URL directly rather than through the sidebar.
 *
 * Both destinations are declared at `wave=2` in `core/navigation.py`, and
 * `ACTIVE_WAVE` stays 0 in this slab on purpose — that bump is a separate,
 * later decision (see `workspace.spec.ts`'s "wave-1 destinations are absent"
 * test, extended to cover these two). So every test here navigates straight
 * to the route, the same way a Wave-1 screen like `quality` would be tested
 * today, before its own wave activates.
 *
 * ORDER WITHIN THIS FILE IS LOAD-BEARING. `workers: 1` /
 * `fullyParallel: false` (see `playwright.config.ts`) means every test in
 * this suite shares ONE mock API process, and the demo plane seeds exactly
 * ONE incident (batch 8842's DQ-002 failure). The read-only tests that
 * observe it OPEN run first; the round trip that walks it all the way to
 * CLOSED runs last, because a closed incident is not an open one and the
 * list stops showing it the moment that happens.
 */

async function signIn(page: Page, who: string) {
  await page.goto("/signin");
  await page.locator(`[data-signin="${who}"]`).click();
  await page.waitForURL("/");
}

const OPERATOR = "dev-operations@cinqcare.test";
const READ_ONLY = "dev-analyst@cinqcare.test";

/* ──────────────────────────────── incidents ─────────────────────────────── */

test("the incidents list shows the seeded batch's root cause and its NOVEL match", async ({
  page,
}) => {
  await signIn(page, OPERATOR);
  const response = await page.goto("/operations/incidents");
  expect(response?.status()).toBeLessThan(400);
  await expect(page.getByRole("heading", { name: "Incidents" })).toBeVisible();

  const row = page.getByRole("row", { name: /8842/ });
  await expect(row).toBeVisible();
  await expect(row.getByText("open", { exact: true })).toBeVisible();
  // The seeded error, from `intelligence/demo.py` — not a fixture invented
  // for this test, the same message `error c0ffee42` carries everywhere else.
  await expect(row.getByText(/rows failed DQ-002/)).toBeVisible();
  // No runbook is published in the demo plane, so nothing can match.
  await expect(row.getByText("Novel", { exact: true })).toBeVisible();
});

test("a read-only principal sees the incident but no working action", async ({ page }) => {
  await signIn(page, READ_ONLY);
  await page.goto("/operations/incidents");
  const row = page.getByRole("row", { name: /8842/ });
  await expect(row).toBeVisible();
  await expect(row.getByRole("button", { name: "Acknowledge" })).toBeDisabled();
  await expect(row.getByRole("button", { name: "Resolve" })).toBeDisabled();
  await expect(page.getByText(/acknowledge is not permitted for your role/).first()).toBeVisible();
});

test("an incident moves from open to closed, one honest step at a time", async ({ page }) => {
  await signIn(page, OPERATOR);
  await page.goto("/operations/incidents");

  const open = page.getByRole("row", { name: /8842/ });
  await expect(open).toBeVisible();
  await open.getByLabel("Assign to").fill("Priya Nair");
  await open.getByRole("button", { name: "Acknowledge" }).click();
  await page.waitForURL(/outcome=ACKNOWLEDGED/);
  await expect(page.locator('[data-outcome="ACKNOWLEDGED"]')).toBeVisible();

  const acknowledged = page.getByRole("row", { name: /8842/ });
  await expect(acknowledged.getByText("acknowledged", { exact: true })).toBeVisible();
  await expect(acknowledged.getByText("Priya Nair")).toBeVisible();
  await acknowledged
    .getByLabel("Resolution")
    .fill("Payer resent the file with date_of_birth populated; reprocessed clean.");
  await acknowledged.getByRole("button", { name: "Resolve" }).click();
  await page.waitForURL(/outcome=RESOLVED/);
  await expect(page.locator('[data-outcome="RESOLVED"]')).toBeVisible();

  const resolved = page.getByRole("row", { name: /8842/ });
  await expect(resolved.getByText("resolved", { exact: true })).toBeVisible();
  await resolved.getByRole("button", { name: "Close" }).click();
  await page.waitForURL(/outcome=CLOSED/);
  await expect(page.locator('[data-outcome="CLOSED"]')).toBeVisible();

  // Closed is not open — the list this page renders excludes it.
  await expect(page.getByRole("row", { name: /8842/ })).toHaveCount(0);
});

/* ─────────────────────────────── certification ──────────────────────────── */

test("the certification overview renders a derived verdict", async ({ page }) => {
  await signIn(page, OPERATOR);
  const response = await page.goto("/operations/certification");
  expect(response?.status()).toBeLessThan(400);
  await expect(page.getByRole("heading", { name: "Certification" })).toBeVisible();

  const row = page.getByRole("row", { name: /8842/ });
  await expect(row).toBeVisible();
  // DQ_RULES has no recorded verdict in the seeded plane, so the batch's own
  // certification is genuinely PENDING — never fabricated as CERTIFIED.
  await expect(row.getByText("Pending", { exact: true })).toBeVisible();
});

test("the batch's own certification page shows every check, PASS/FAIL/PENDING", async ({
  page,
}) => {
  await signIn(page, OPERATOR);
  await page.goto("/operations/certification");
  await page.getByRole("link", { name: /see the evidence/i }).click();
  await page.waitForURL(/\/operations\/certification\/batch\/8842$/);

  await expect(page.getByRole("heading", { name: "Batch 8842" })).toBeVisible();
  await expect(page.getByText("Pending", { exact: true }).first()).toBeVisible();
  for (const kind of ["balance", "reconciliation", "drop_ledger", "dq_rules", "schema_contract"]) {
    await expect(page.getByText(kind, { exact: true })).toBeVisible();
  }
  // dq_rules is the one check with nothing recorded — PENDING, not FAIL.
  await expect(page.getByText("PENDING", { exact: true })).toBeVisible();
  await expect(page.getByText("no rule verdicts recorded")).toBeVisible();
});

test("the operator can export the certification evidence as byte-comparable text", async ({
  page,
}) => {
  await signIn(page, OPERATOR);
  await page.goto("/operations/certification/batch/8842");
  const link = page.getByRole("link", { name: /export evidence/i });
  await expect(link).toBeVisible();

  const href = await link.getAttribute("href");
  const response = await page.request.get(href!);
  expect(response.status()).toBe(200);
  expect(response.headers()["content-type"]).toContain("text/plain");
  const body = await response.text();
  expect(body).toContain("8842");
});

test("a role without certify_export is told so, not handed a link that 403s", async ({ page }) => {
  await signIn(page, READ_ONLY);
  await page.goto("/operations/certification/batch/8842");
  await expect(page.getByRole("link", { name: /export evidence/i })).toHaveCount(0);
  await expect(page.getByText(/certify_export/)).toBeVisible();
});
