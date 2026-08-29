import { expect, test, type Page } from "@playwright/test";
import { STATUS_WORDS } from "../lib/types";

/**
 * The design system's own gates.
 *
 * Everything here was a comment in a stylesheet or a paragraph in a proposal
 * before it was a test. A design rule that nothing checks survives exactly as
 * long as the person who wrote it stays on the project.
 */

async function signIn(page: Page, who: string) {
  await page.goto("/signin");
  await page.locator(`[data-signin="${who}"]`).click();
  await page.waitForURL("/");
}

const DESTINATIONS = [
  "/",
  "/data/intake",
  "/data/explorer",
  "/operations/monitor",
  "/operations/control",
  "/ai/ask",
  "/ai/observability",
  "/admin/audit",
];

/* ────────────────────────────────── theme ───────────────────────────────── */

test("the platform is black text on a white background", async ({ page }) => {
  await signIn(page, "dev-engineer@cinqcare.test");
  const { bg, ink } = await page.evaluate(() => {
    const style = getComputedStyle(document.body);
    return { bg: style.backgroundColor, ink: style.color };
  });
  expect(bg).toBe("rgb(255, 255, 255)");
  expect(ink).toBe("rgb(20, 23, 26)");
});

test("body text clears WCAG AA against the page", async ({ page }) => {
  // Computed here rather than asserted as a magic number, so a designer
  // retuning the palette gets a failure with the actual ratio in it.
  await signIn(page, "dev-engineer@cinqcare.test");
  const ratio = await page.evaluate(() => {
    const parse = (value: string) => value.match(/\d+/g)!.map(Number).slice(0, 3);
    const lum = ([r, g, b]: number[]) => {
      const lin = (c: number) => {
        const s = c / 255;
        return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
      };
      return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
    };
    const style = getComputedStyle(document.body);
    const a = lum(parse(style.color));
    const b = lum(parse(style.backgroundColor));
    return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
  });
  expect(ratio).toBeGreaterThanOrEqual(4.5);
});

/* ───────────────────────────────── lexicon ──────────────────────────────── */

test("each of the seven status words has its OWN colour", async ({ page }) => {
  /**
   * The dark theme gave Needs Attention and Missing the same hex, so two of
   * the seven were indistinguishable on every screen. They are different
   * facts — "an issue requires action" and "expected data has not arrived" —
   * and a screen that cannot separate them cannot be acted on.
   */
  await signIn(page, "dev-engineer@cinqcare.test");
  const colours = await page.evaluate((words) => {
    const probe = document.createElement("span");
    probe.className = "status";
    document.body.append(probe);
    const seen: Record<string, string> = {};
    for (const word of words) {
      probe.setAttribute("data-word", word);
      seen[word] = getComputedStyle(probe).color;
    }
    probe.remove();
    return seen;
  }, STATUS_WORDS as unknown as string[]);

  const distinct = new Set(Object.values(colours));
  expect(distinct.size, `collisions: ${JSON.stringify(colours)}`).toBe(STATUS_WORDS.length);
});

test("a status word carries a SHAPE, not only a colour", async ({ page }) => {
  // WCAG 1.4.1: meaning must survive greyscale. Roughly one man in twelve
  // cannot separate the red from the green reliably.
  await signIn(page, "dev-engineer@cinqcare.test");
  await page.goto("/operations/control");
  const marks = page.locator(".status .mark");
  expect(await marks.count()).toBeGreaterThan(0);
  // The mark is decoration; the word is the accessible name.
  await expect(marks.first()).toHaveAttribute("aria-hidden", "true");
});

/* ──────────────────────────────── the drawer ────────────────────────────── */

test("clicking a run OVERLAYS the drawer and keeps the list behind it", async ({ page }) => {
  /**
   * ADR-0020 says depth is a drawer, never an IA branch. The routing half was
   * always right; the drawer was a full PAGE, so clicking a row threw away the
   * list you were reading. This is the assertion that it no longer does.
   */
  await signIn(page, "dev-engineer@cinqcare.test");
  await page.goto("/operations/control");
  await page.getByRole("link", { name: "8842" }).first().click();

  const drawer = page.getByRole("dialog");
  await expect(drawer).toBeVisible();
  await expect(drawer).toHaveAttribute("aria-modal", "true");
  // The list is still mounted underneath — that is the whole point.
  await expect(page.getByRole("heading", { name: "Control Operations" })).toBeVisible();
});

test("Escape closes the drawer and returns to the list", async ({ page }) => {
  await signIn(page, "dev-engineer@cinqcare.test");
  await page.goto("/operations/control");
  await page.getByRole("link", { name: "8842" }).first().click();
  await expect(page.getByRole("dialog")).toBeVisible();

  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page).toHaveURL(/\/operations\/control$/);
});

test("a pasted drawer URL renders the full page, not an empty shell", async ({ page }) => {
  // A shared address must not depend on the route somebody came from —
  // "look at recon:8842#DQ-002" is what replaces a screenshot in Slack.
  await signIn(page, "dev-engineer@cinqcare.test");
  await page.goto("/operations/control/batch/8842?panel=recon");
  await expect(page.getByRole("heading", { name: "Batch 8842" })).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByText("rows_in == rows_out")).toBeVisible();
});

test("the five panels are announced as a tab set", async ({ page }) => {
  await signIn(page, "dev-engineer@cinqcare.test");
  await page.goto("/operations/control/batch/8842");
  const tablist = page.getByRole("tablist");
  await expect(tablist).toBeVisible();
  expect(await tablist.getByRole("tab").count()).toBe(5);
});

/* ────────────────────────────────── persona ─────────────────────────────── */

test("every Wave-0 persona gets a ranked home, and the SAME words", async ({ page }) => {
  /**
   * The merge-rule tripwire, widened from two roles to all three Wave 0 ships.
   * If this has to be weakened, ADR-0020 is what should be revisited.
   *
   * Wave 0 has three roles by design — the seven-role matrix is CF-V4-E2-02.
   */
  const homes: Record<string, string> = {
    "dev-engineer@cinqcare.test": "What needs you",
    "dev-analyst@cinqcare.test": "What arrived",
    "dev-admin@cinqcare.test": "What the platform refused",
  };

  for (const [who, heading] of Object.entries(homes)) {
    await signIn(page, who);
    await expect(page.getByRole("heading", { level: 1, name: heading })).toBeVisible();
    // Same destinations, same labels — only the ranking moved.
    await expect(page.locator('[data-destination="control"]')).toBeVisible();
    await expect(page.locator('[data-destination="home"]')).toBeVisible();
  }
});

test("persona changes the RANK, never the vocabulary", async ({ page }) => {
  const labels = async () =>
    (await page.locator("[data-destination]").allInnerTexts()).map((t) => t.split("\n")[0]).sort();

  await signIn(page, "dev-engineer@cinqcare.test");
  const engineer = await labels();

  await signIn(page, "dev-analyst@cinqcare.test");
  const analyst = await labels();

  // Admin sees Users & Roles, which is a PERMISSION difference, not a persona
  // one — so the two non-admin roles must agree exactly.
  expect(engineer).toEqual(analyst);
});

/* ──────────────────────────── keyboard + landmarks ──────────────────────── */

test("a keyboard user can skip the navigation", async ({ page }) => {
  // Not asserted as "the first Tab", because `next dev` injects its own
  // devtools control ahead of the app in tab order — an artefact of the dev
  // server, not of the page. What matters is the behaviour: the link is
  // off-screen until focused, and it lands you past fifteen nav items.
  await signIn(page, "dev-engineer@cinqcare.test");
  const skip = page.getByRole("link", { name: "Skip to content" });
  await expect(skip).toBeAttached();

  await skip.focus();
  await expect(skip).toBeInViewport();

  await skip.press("Enter");
  await expect(page).toHaveURL(/#main$/);
});

test("every destination has a labelled nav and a main landmark", async ({ page }) => {
  await signIn(page, "dev-engineer@cinqcare.test");
  for (const route of DESTINATIONS) {
    await page.goto(route);
    await expect(page.getByRole("navigation", { name: "Primary" })).toBeVisible();
    await expect(page.getByRole("main")).toBeVisible();
  }
});

test("focus is always visible", async ({ page }) => {
  /**
   * There was no focus style in the entire stylesheet, which made the app
   * keyboard-unusable in practice: you could tab through it, but never see
   * where you were. Driven from the keyboard rather than `.focus()`, because
   * `:focus-visible` is precisely the rule that distinguishes the two.
   */
  await signIn(page, "dev-engineer@cinqcare.test");

  let landed = false;
  for (let attempt = 0; attempt < 20 && !landed; attempt += 1) {
    await page.keyboard.press("Tab");
    landed = await page.evaluate(() =>
      Boolean(document.activeElement?.classList.contains("dest")),
    );
  }
  expect(landed, "never reached a navigation destination by keyboard").toBe(true);

  const outline = await page.evaluate(() => {
    const style = getComputedStyle(document.activeElement!);
    return { width: style.outlineWidth, style: style.outlineStyle };
  });
  expect(outline.style).not.toBe("none");
  expect(parseFloat(outline.width)).toBeGreaterThan(0);
});
