import { expect, test, type Page } from "@playwright/test";
import { STATUS_WORDS } from "../lib/types";

/**
 * The UI half of the twin end-to-end run.
 *
 * The script the Wave-0 exit asserts, in a browser: sign in as Engineer, see
 * the batch ranked, open Data Intake, open the batch drawer, click the recon
 * figure, land on the citation — then sign in as Read-Only and paste the edit
 * URL, and be denied at the SERVER with the attempt in the audit trail.
 */

async function signIn(page: Page, who: string) {
  await page.goto("/signin");
  await page.locator(`[data-signin="${who}"]`).click();
  await page.waitForURL("/");
}

test("a signed-out visitor reaches sign-in, never a broken shell", async ({ page }) => {
  await page.context().clearCookies();
  await page.goto("/");
  await expect(page).toHaveURL(/\/signin/);
  await expect(page.getByRole("heading", { name: "Sign in to CINQFLOW" })).toBeVisible();
  // No password field here, and never will be: the platform verifies a token
  // somebody else issued and holds no credential of its own.
  await expect(page.locator('input[type="password"]')).toHaveCount(0);
});

test("a user in no CINQFLOW group gets a clear answer, not an empty app", async ({ page }) => {
  await page.goto("/signin");
  await page.locator('[data-signin="dev-nogroup@cinqcare.test"]').click();
  await page.waitForURL(/no-access/);
  await expect(page.getByRole("heading", { name: "No access assigned" })).toBeVisible();
  await expect(page.getByText("Contact your administrator")).toBeVisible();
});

test("wave-1 destinations are absent, not greyed out", async ({ page }) => {
  await signIn(page, "dev-engineer@cinqcare.test");
  for (const key of ["mapping", "quality", "work-queue", "lineage", "incidents", "certification"]) {
    await expect(page.locator(`[data-destination="${key}"]`)).toHaveCount(0);
  }
  await expect(page.locator('[data-destination="intake"]')).toBeVisible();
  await expect(page.locator('[data-destination="llm-observability"]')).toBeVisible();
});

test("persona ranks the home without changing the words", async ({ page }) => {
  await signIn(page, "dev-engineer@cinqcare.test");
  await expect(page.getByRole("heading", { name: "What needs you" })).toBeVisible();

  await signIn(page, "dev-analyst@cinqcare.test");
  await expect(page.getByRole("heading", { name: "What arrived" })).toBeVisible();
  // Same destinations, same labels — only the ranking moved.
  await expect(page.locator('[data-destination="control"]')).toBeVisible();
});

test("a citation is a route: the recon figure opens the row it cites", async ({ page }) => {
  await signIn(page, "dev-engineer@cinqcare.test");
  await page.goto("/operations/control");
  await page.getByRole("link", { name: "8842" }).first().click();
  await page.waitForURL(/\/operations\/control\/batch\/8842/);
  await expect(page.getByRole("heading", { name: "Batch 8842" })).toBeVisible();

  // The one drawer, five panels, one depth level.
  for (const panel of ["stages", "inputs", "errors", "quarantine", "recon"]) {
    await expect(page.locator(`[data-panel="${panel}"]`)).toBeVisible();
  }
  await page.locator('[data-panel="recon"]').click();
  await expect(page.getByText("rows_in == rows_out")).toBeVisible();
});

test("the batch drawer offers no write buttons", async ({ page }) => {
  await signIn(page, "dev-engineer@cinqcare.test");
  await page.goto("/operations/control/batch/8842?panel=recon");
  await expect(page.getByText("This drawer has no write buttons")).toBeVisible();
  for (const word of ["Retry", "Reprocess", "Pause"]) {
    await expect(page.getByRole("button", { name: word })).toHaveCount(0);
  }
});

test("asking a question returns cited claims and a trace", async ({ page }) => {
  await signIn(page, "dev-analyst@cinqcare.test");
  await page.goto("/ai/ask");
  await page.locator("[data-ask-input]").fill("why did batch 8842 lose rows?");
  await page.getByRole("button", { name: "Ask" }).click();
  await page.waitForURL(/\/ai\/ask\?q=/);

  await expect(page.locator("[data-answer]")).toBeVisible();
  const claims = page.locator("[data-claim]");
  expect(await claims.count()).toBeGreaterThan(0);
  // Every claim carries at least one citation chip.
  for (let index = 0; index < (await claims.count()); index += 1) {
    await expect(claims.nth(index).locator(".chip")).not.toHaveCount(0);
  }
  await expect(page.getByText("How I got there")).toBeVisible();
  await expect(page.getByText("route", { exact: true })).toBeVisible();
});

test("clicking a citation in an answer opens that drawer", async ({ page }) => {
  await signIn(page, "dev-analyst@cinqcare.test");
  await page.goto(`/ai/ask?q=${encodeURIComponent("why did batch 8842 lose rows?")}`);
  await page.locator("[data-claim] .chip").first().click();
  await expect(page).toHaveURL(/\/operations\/control\/batch\/8842/);
});

test("asking the agent to retry a batch is refused and explained", async ({ page }) => {
  await signIn(page, "dev-analyst@cinqcare.test");
  await page.goto(`/ai/ask?q=${encodeURIComponent("retry batch 8842")}`);
  const refusal = page.locator("[data-refusal]");
  await expect(refusal).toBeVisible();
  await expect(refusal).toContainText("R0");
  await expect(refusal).toContainText("CF-V1-E16-06");
});

test("a member-level question is declined by name", async ({ page }) => {
  await signIn(page, "dev-analyst@cinqcare.test");
  await page.goto(`/ai/ask?q=${encodeURIComponent("what is a member's date of birth?")}`);
  const refusal = page.locator("[data-refusal]");
  await expect(refusal).toBeVisible();
  await expect(refusal).toContainText("CF-V4-E14-04");
});

test("cost, refusals and grounding are on a screen", async ({ page }) => {
  await signIn(page, "dev-analyst@cinqcare.test");
  await page.goto(`/ai/ask?q=${encodeURIComponent("retry batch 8842")}`);
  await page.goto("/ai/observability");
  await expect(page.getByText("Spent today")).toBeVisible();
  await expect(page.getByText("Refusals today")).toBeVisible();
  await expect(page.getByText("Uncited claims blocked")).toBeVisible();
  // The refusal we just caused is visible as a row.
  await expect(page.locator('[data-outcome="refused_not_whitelisted"]').first()).toBeVisible();
});

test("READ-ONLY crafting an edit URL is denied at the SERVER", async ({ page, request }) => {
  await signIn(page, "dev-analyst@cinqcare.test");

  // The menu hides nothing here — the refusal is the control, not the hiding.
  const denied = await request.put(
    "http://127.0.0.1:8100/api/feeds/fidelis-downstate-roster",
    {
      headers: { authorization: "Bearer dev-analyst@cinqcare.test" },
      data: { feed_id: "fidelis-downstate-roster" },
    },
  );
  expect(denied.status()).toBe(403);
  expect(await denied.text()).toContain("not permitted");

  // ...and the attempt is in the audit trail.
  await signIn(page, "dev-admin@cinqcare.test");
  await page.goto("/admin/audit");
  await expect(page.locator('[data-action="denied:edit_feed"]').first()).toBeVisible();
});

test("only an administrator sees Users & Roles", async ({ page }) => {
  await signIn(page, "dev-engineer@cinqcare.test");
  await expect(page.locator('[data-destination="users"]')).toHaveCount(0);

  await signIn(page, "dev-admin@cinqcare.test");
  await expect(page.locator('[data-destination="users"]')).toBeVisible();
  await page.goto("/admin/users");
  await expect(page.getByText("segregation of duties")).toBeVisible();
});

test("no eighth status word reaches a rendered surface", async ({ page }) => {
  /**
   * The lexicon gate. The CI lexicon check is a Wave-2 gate, but this costs
   * nothing now and prevents exactly the dialects it exists to catch:
   * "Success", "Failed", "Warning", "OK", "Error" on a screen next to the
   * seven official words.
   */
  const DIALECTS = ["Success", "Successful", "Failure", "Warning", "OK", "In Progress", "Pending"];
  await signIn(page, "dev-engineer@cinqcare.test");

  for (const route of [
    "/",
    "/data/intake",
    "/data/explorer",
    "/operations/monitor",
    "/operations/control",
    "/operations/control/batch/8842?panel=recon",
    "/admin/audit",
  ]) {
    await page.goto(route);
    // Only inside status chips — prose may legitimately say "failed a rule".
    const words = await page.locator(".status").allInnerTexts();
    for (const rendered of words) {
      expect(STATUS_WORDS as readonly string[]).toContain(rendered.trim());
    }
    for (const dialect of DIALECTS) {
      await expect(page.locator(`.status:text-is("${dialect}")`)).toHaveCount(0);
    }
  }
});
