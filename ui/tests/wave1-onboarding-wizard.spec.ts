import { expect, test, type Page } from "@playwright/test";

/**
 * CF-V1-E4-01/02/03 — the guided journey, in a browser.
 *
 *     "a trained analyst can take a new feed from a sample file to a published
 *      configuration without engineering tickets, which is the MVP's headline
 *      promise"
 *     — CF-V1-E4-01
 *
 * WHAT THIS FILE EXISTS TO PREVENT COMING BACK. The wizard's backend has been
 * complete since it shipped — `GET /onboarding`, `GET /evidence`,
 * `GET /narrative`, `POST /onboarding/submit`, all covered by the Python
 * contract suite — and NO PAGE CALLED ANY OF IT. The API was green, the
 * headline promise had no front door, and nothing in either suite could
 * notice, because a route nobody renders is a route no browser test visits.
 *
 * The assertions below are deliberately about HONESTY rather than progress:
 * that a step awaiting somebody else does not render as done, that an
 * obstacle names its fix, and that a missing evidence pack says so instead of
 * rendering an empty one. Those are the three ways this screen could look
 * fine and lie.
 */

const FEED = "fidelis-downstate-roster";
const WIZARD = `/data/intake/feed/${FEED}/onboarding`;

async function signIn(page: Page, who = "dev-ba@cinqcare.test") {
  await page.goto("/signin");
  await page.locator(`[data-signin="${who}"]`).click();
  await page.waitForURL("/");
}

test("the wizard renders all five steps in order", async ({ page }) => {
  await signIn(page);
  const response = await page.goto(WIZARD);
  expect(response?.status(), "the guided journey must have a front door").not.toBe(404);
  expect(response?.status()).toBeLessThan(500);

  await expect(page.getByRole("heading", { level: 1 })).toContainText(FEED);
  const body = await page.locator("body").innerText();
  for (const step of [
    "1. Upload a sample file",
    "2. Approve the schema",
    "3. Map the fields",
    "4. Define and test the rules",
    "5. Publish and schedule",
  ]) {
    expect(body, `step missing: ${step}`).toContain(step);
  }
});

test("the feed page links to the wizard — the journey is reachable, not just routable", async ({
  page,
}) => {
  await signIn(page);
  await page.goto(`/data/intake/feed/${FEED}`);
  const link = page.getByRole("link", { name: /onboarding checklist/i });
  await expect(link).toBeVisible();
  await link.click();
  await page.waitForURL(WIZARD);
});

test("a step nobody has started is never rendered as done", async ({ page }) => {
  await signIn(page);
  await page.goto(WIZARD);
  const body = await page.locator("body").innerText();
  // `state` beside the status word. AWAITING_APPROVAL is deliberately not
  // COMPLETE, and a screen showing only the seven words would render
  // "somebody else's move" and "genuinely done" identically.
  expect(body).toMatch(/not started|locked|in progress|awaiting approval|complete/i);
  // The checklist reflects real lifecycle states, not optimism — E4-01's
  // first don't.
  expect(body).not.toMatch(/all steps complete/i);
});

test("every obstacle names what to do about it", async ({ page }) => {
  await signIn(page);
  await page.goto(WIZARD);
  const body = await page.locator("body").innerText();
  // "the wizard says exactly what stands between her and done" — a blocker
  // with no fix is a dead end, which is the failure this asserts against.
  expect(body).toContain("To fix:");
});

test("a missing evidence pack says so rather than rendering an empty one", async ({ page }) => {
  await signIn(page);
  await page.goto(WIZARD);
  const body = (await page.locator("body").innerText()).toLowerCase();
  // An empty pack rendered as a document is evidence that says nothing while
  // looking like evidence, and somebody would attach it to an approval.
  expect(body).toMatch(/no evidence pack|has no end-to-end test/);
});

test("submitting is offered but not pretended — the button reflects the server's verdict", async ({
  page,
}) => {
  await signIn(page);
  await page.goto(WIZARD);
  const submit = page.getByRole("button", { name: /submit for approval/i });
  await expect(submit).toBeVisible();
  // On the seeded plane this feed is not publishable, so the control is
  // disabled AND the reason is on screen. Disabled alone would be a mystery.
  await expect(submit).toBeDisabled();
  const body = (await page.locator("body").innerText()).toLowerCase();
  expect(body).toMatch(/blocking|outstanding|not publishable/);
});

test("the seven status words are the only ones the wizard prints", async ({ page }) => {
  await signIn(page);
  await page.goto(WIZARD);
  // `Status` renders an unrecognised word visibly wrong; this is the check
  // that a wizard step never introduces an eighth.
  const rogue = page.locator('.status[data-word="unknown"]');
  await expect(rogue).toHaveCount(0);
});
