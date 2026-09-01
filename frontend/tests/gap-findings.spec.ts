/**
 * Wave-0 validation audit (2026-08-29) — citation dead-ends, as executable tests.
 *
 * The backend emits ten citation kinds; the router (core/citations and
 * ui/lib/citations.ts, byte-identical grammar) generates a route for each.
 * Seven of those routes had no page under ui/app, so a well-formed citation
 * chip in an agent answer opened Next's default 404 — evidence that resolved
 * to nothing. Six are now real pages served by certified tools through the
 * generic /api/tools/{name} route; `mapping` — the one kind no Wave-0 tool
 * emits — gets an honest "not yet available" page instead of fabricated data.
 * Mirrors tests/audit/test_wave0_gap_findings.py.
 */
import { test, expect } from "@playwright/test";

const ALL_CITATION_ROUTES: ReadonlyArray<[kind: string, route: string]> = [
  ["feed", "/data/intake/feed/fidelis-downstate-roster"],
  ["contract", "/data/intake/contract/fidelis-downstate-roster"],
  ["plan", "/data/intake/feed/fidelis-downstate-roster/plan"],
  ["mapping", "/data/intake/mapping/fidelis-downstate-roster"],
  ["batch", "/operations/control/batch/8842?panel=stages"],
  ["recon", "/operations/control/batch/8842?panel=recon&drop=DQ-002"],
  ["error", "/operations/control/error/a41f9c2e"],
  ["file", "/data/explorer/landing/a41f9c2e"],
  ["rule", "/data/intake/rule/DQ-002"],
  ["term", "/data/intake/glossary/npi"],
];

for (const [kind, route] of ALL_CITATION_ROUTES) {
  test(`the ${kind}: citation deep-links to a real page`, async ({ page }) => {
    const response = await page.goto(route);
    expect(response?.status(), `${route} must not 404`).not.toBe(404);
  });
}
