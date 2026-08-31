import { expect, test, type Page } from "@playwright/test";

/**
 * W1-31 · CF-V1-E6-03 — the first write-capable proposal review, driven end
 * to end against the mapping-suggestion proposal `intelligence/demo.py`
 * seeds for exactly this purpose (`MAPPING_PROPOSAL_ID`, feed
 * `meridian-member-roster` — a feed of its own, never
 * `fidelis-downstate-roster`: that feed's absence of a mapping is what
 * `wave1-intake.spec.ts` asserts).
 *
 * The seeded proposal poses a real CF-V1-E6-04 question on purpose: it
 * repeats the feed's own published v1 for two columns, proposes a fresh
 * target for the one v1 left unmapped, and proposes DROPPING the source
 * `effective_date` already has in v1. Accepting that drop as written and then
 * approving the resulting draft is exactly the silent-row-loss scenario
 * `accepts_loss` exists to make a named, deliberate act instead of a click
 * nobody reads — which is the reason this suite checks EVERY box the diff
 * lists rather than picking one: `refuse_unacknowledged_loss` refuses an
 * approval that leaves any of them unnamed.
 *
 * ORDER WITHIN THIS FILE IS LOAD-BEARING, the same reason
 * `wave2-ops-screens.spec.ts` gives: `workers: 1` / `fullyParallel: false`
 * means every test here shares ONE mock API process and therefore one
 * mutable proposal. The read-only check runs first, before anything moves;
 * the full accept-through-publish journey runs last, because a published
 * mapping is not a pending proposal and nothing after it could re-run the
 * same steps.
 */

const PROPOSAL_ID = "mapping-suggestion-meridian-member-roster-1";
const FEED_ID = "meridian-member-roster";
const READ_ONLY = "dev-analyst@cinqcare.test";
const BUSINESS_ANALYST = "dev-ba@cinqcare.test";
const DATA_STEWARD = "dev-steward@cinqcare.test";

async function signIn(page: Page, who: string) {
  await page.goto("/signin");
  await page.locator(`[data-signin="${who}"]`).click();
  await page.waitForURL("/");
}

test("a read-only principal reads the mapping proposal but presses nothing", async ({ page }) => {
  await signIn(page, READ_ONLY);
  const response = await page.goto(`/data/intake/proposals/${PROPOSAL_ID}`);
  expect(response?.status()).toBeLessThan(400);
  await expect(page.getByRole("heading", { name: "Where each column would land" })).toBeVisible();

  // The four seeded lines are rendered, editable inputs and all — reading is
  // still VIEW, which a read-only principal holds.
  await expect(page.getByLabel("plan_cd target field")).toHaveValue("line_of_business");
  await expect(page.getByLabel("eff_dt unmapped", { exact: true })).toBeChecked();

  await expect(page.getByRole("button", { name: "Accept" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Reject" })).toBeDisabled();
  await expect(page.getByText(/edit_feed\. Your role can read this screen/).first()).toBeVisible();
});

test("a mapping proposal goes through its full real lifecycle end to end", async ({ page, request }) => {
  // ── the business analyst accepts, retargeting one line and keeping the
  //    agent's proposed drop of another ────────────────────────────────────
  await signIn(page, BUSINESS_ANALYST);
  await page.goto(`/data/intake/proposals/${PROPOSAL_ID}`);
  await expect(page.getByRole("heading", { name: "Where each column would land" })).toBeVisible();

  // EDIT A LINE'S TARGET before accepting — a real form control, not text.
  await page.getByLabel("plan_cd target field").fill("plan_family");

  // Leave `eff_dt` exactly as the agent proposed it: unmapped, no source.
  // That is the real loss `accepts_loss` will have to name two steps from
  // now — accepting a decline is a legitimate answer, not a bug to route
  // around in this test.
  await expect(page.getByLabel("eff_dt unmapped", { exact: true })).toBeChecked();

  await page.getByLabel("Comment (optional)").first().fill("Retargeting plan_cd; eff_dt stands.");
  await page.getByRole("button", { name: "Accept" }).click();
  await page.waitForURL(/outcome=ACCEPTED/);
  await expect(page.locator('[data-outcome="ACCEPTED"]')).toBeVisible();
  await expect(page.getByText(/draft mapping is now waiting/)).toBeVisible();

  // The correction is already on the wire, before any lifecycle act moves
  // the draft further — `GET /api/feeds/{id}/mapping` has no lifecycle gate.
  const afterAccept = await request.get(`http://127.0.0.1:8100/api/feeds/${FEED_ID}/mapping`, {
    headers: { Authorization: `Bearer ${BUSINESS_ANALYST}` },
  });
  expect(afterAccept.ok()).toBeTruthy();
  const draftBody = await afterAccept.json();
  expect(draftBody.lifecycle_state).toBe("draft");
  expect(draftBody.version).toBe(2);
  const planLine = draftBody.lines.find((l: { target_field: string }) => l.target_field === "plan_family");
  expect(planLine?.source_columns).toEqual(["plan_cd"]);
  const effLine = draftBody.lines.find((l: { target_field: string }) => l.target_field === "effective_date");
  expect(effLine?.status).toBe("unmapped");

  // The BA carries their own draft to review — SUBMIT_FOR_REVIEW is theirs.
  await expect(page.getByRole("heading", { name: "The draft mapping this produced" })).toBeVisible();
  await page.getByRole("button", { name: "Submit for review" }).click();
  await page.waitForURL(/outcome=SUBMITTED/);
  await expect(page.locator('[data-outcome="SUBMITTED"]')).toBeVisible();

  // ── a data steward — never the BA who authored the draft — approves and
  //    publishes ────────────────────────────────────────────────────────
  await signIn(page, DATA_STEWARD);
  await page.goto(`/data/intake/proposals/${PROPOSAL_ID}`);
  await expect(page.getByText(/field.*would stop being populated/)).toBeVisible();

  const lossBoxes = page.locator('input[name="accepts_loss"]');
  const lossCount = await lossBoxes.count();
  expect(lossCount).toBeGreaterThan(0);
  for (let i = 0; i < lossCount; i += 1) {
    await lossBoxes.nth(i).check();
  }

  // `lifecycle.approve` requires a stated rationale — it becomes part of the
  // audit record, and an unexplained approval is the rubber stamp the
  // platform refuses to make available.
  await page.getByLabel("Comment (optional)").fill("Acknowledging the eff_dt drop and the plan_cd retarget.");
  await page.getByRole("button", { name: "Approve" }).click();
  await page.waitForURL(/outcome=APPROVED/);
  await expect(page.locator('[data-outcome="APPROVED"]')).toBeVisible();

  await page.getByRole("button", { name: "Publish" }).click();
  await page.waitForURL(/outcome=PUBLISHED/);
  await expect(page.locator('[data-outcome="PUBLISHED"]')).toBeVisible();
  await expect(page.getByText(/is live/)).toBeVisible();

  // ── the resulting mapping version, read back exactly as the task asks —
  //    through the real, existing route, not through this page's own render ─
  const published = await request.get(`http://127.0.0.1:8100/api/feeds/${FEED_ID}/mapping`, {
    headers: { Authorization: `Bearer ${DATA_STEWARD}` },
  });
  expect(published.ok()).toBeTruthy();
  const body = await published.json();
  expect(body.lifecycle_state).toBe("published");
  expect(body.version).toBe(2);

  const published_plan = body.lines.find(
    (l: { target_field: string }) => l.target_field === "plan_family",
  );
  expect(published_plan?.source_columns).toEqual(["plan_cd"]);
  expect(published_plan?.status).toBe("mapped");

  const published_eff = body.lines.find(
    (l: { target_field: string }) => l.target_field === "effective_date",
  );
  expect(published_eff?.source_columns).toEqual([]);
  expect(published_eff?.status).toBe("unmapped");

  // Untouched lines survived the round trip unchanged.
  const memberId = body.lines.find(
    (l: { target_field: string }) => l.target_field === "source_member_id",
  );
  expect(memberId?.source_columns).toEqual(["member_id"]);
});
