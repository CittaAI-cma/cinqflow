import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

/**
 * Accessibility, as a gate rather than an intention.
 *
 * The UI shipped with no focus style, no skip link, no landmark labels, no
 * table semantics, and a defect signal (`.uncited`) carried by colour alone.
 * None of that was a decision — there was simply nothing that would fail if it
 * were wrong.
 *
 * This runs axe over every active destination. Scoped to WCAG 2.1 A and AA,
 * because those are the levels an enterprise-healthcare procurement will ask
 * about, and a rule set nobody can satisfy is a rule set that gets skipped.
 */

async function signIn(page: Page, who: string) {
  await page.goto("/signin");
  await page.locator(`[data-signin="${who}"]`).click();
  await page.waitForURL("/");
}

const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"];

/** A violation must name the node that caused it — otherwise every failure
 *  starts with a bisect to find out which element axe meant. */
function describe(violations: { id: string; help: string; nodes: { target: unknown[] }[] }[]) {
  return violations.map(
    (v) => `${v.id} — ${v.help} @ ${v.nodes.map((n) => JSON.stringify(n.target)).join(", ")}`,
  );
}

const DESTINATIONS: ReadonlyArray<[name: string, route: string]> = [
  ["Home", "/"],
  ["Data Intake", "/data/intake"],
  ["Data Explorer", "/data/explorer"],
  ["Monitor", "/operations/monitor"],
  ["Control Operations", "/operations/control"],
  ["Ask CINQFLOW", "/ai/ask"],
  ["LLM Observability", "/ai/observability"],
  ["Audit Trail", "/admin/audit"],
];

for (const [name, route] of DESTINATIONS) {
  test(`${name} has no accessibility violations`, async ({ page }) => {
    await signIn(page, "dev-engineer@cinqcare.test");
    await page.goto(route);

    const { violations } = await new AxeBuilder({ page })
      .withTags(TAGS)
      // The dev server injects its own overlay; it is not shipped.
      .exclude("nextjs-portal")
      .analyze();

    expect(describe(violations), `${route} violations`).toEqual([]);
  });
}

test("the delivery form has no accessibility violations", async ({ page }) => {
  // A form is where a11y bugs actually live — an unlabelled control, a
  // required field announced to nobody, a status colour carrying meaning on
  // its own. This is the only form in the intake stack a person fills in from
  // scratch, and the drop zone it carries is custom.
  await signIn(page, "dev-engineer@cinqcare.test");
  await page.goto("/data/intake/deliver");
  const { violations } = await new AxeBuilder({ page })
    .withTags(TAGS)
    .exclude("nextjs-portal")
    .analyze();
  expect(describe(violations)).toEqual([]);
});

test("Users & Roles has no accessibility violations", async ({ page }) => {
  await signIn(page, "dev-admin@cinqcare.test");
  await page.goto("/admin/users");
  const { violations } = await new AxeBuilder({ page })
    .withTags(TAGS)
    .exclude("nextjs-portal")
    .analyze();
  expect(describe(violations)).toEqual([]);
});

test("the open drawer is a valid modal", async ({ page }) => {
  // Modal semantics are where a11y bugs hide: a dialog with no name, or one
  // that leaves the page behind it reachable, reads as a broken app rather
  // than an inaccessible one.
  await signIn(page, "dev-engineer@cinqcare.test");
  await page.goto("/operations/control");
  await page.getByRole("link", { name: "8842" }).first().click();
  await expect(page.getByRole("dialog")).toBeVisible();
  // Analyse the SETTLED modal, on two counts:
  //
  //  · the page behind it must already be inert, or axe walks a tree the
  //    drawer is about to remove;
  //  · the drawer's entrance animation must have finished. It starts at
  //    opacity 0, and a half-transparent panel lets the scrim bleed through —
  //    so every label inside it fails contrast for as long as the animation
  //    is running. That is a frame, not a defect, and asserting on it is
  //    asserting on the animation rather than on the design.
  await expect
    .poll(() => page.evaluate(() => (document.querySelector("#main") as HTMLElement)?.inert))
    .toBe(true);
  await expect
    .poll(() =>
      page.evaluate(() =>
        document
          .querySelector(".drawer")
          ?.getAnimations()
          .every((animation) => animation.playState === "finished"),
      ),
    )
    .toBe(true);

  const { violations } = await new AxeBuilder({ page })
    .withTags(TAGS)
    .exclude("nextjs-portal")
    .analyze();
  expect(describe(violations)).toEqual([]);
});
