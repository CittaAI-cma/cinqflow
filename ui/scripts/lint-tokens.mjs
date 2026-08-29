/**
 * The token gate.
 *
 * "No colour literal, no raw spacing px, outside globals.css" is the rule that
 * keeps a design system a system rather than a stylesheet plus habits. A rule
 * nothing checks is a rule that survives exactly as long as the person who
 * wrote it stays on the project — the previous stylesheet had twelve distinct
 * spacing values in 130 lines, none of them from a scale, and nobody had done
 * anything wrong.
 *
 * Deliberately narrow, so it can be trusted: it flags colour literals and
 * geometric px, and it ignores the places where a raw value is genuinely the
 * clearest thing (1px hairlines, 0, viewBox coordinates in an SVG mark).
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

const ROOTS = ["app", "components", "lib"];
const ALLOW_FILE = /globals\.css$/;
const HEX = /#[0-9a-fA-F]{3,8}\b/g;
const FUNC_COLOUR = /\b(?:rgb|rgba|hsl|hsla|oklch|lab)\(/g;
const PX = /\b(\d+)px\b/g;
/** 1px is a hairline and 0px is zero — neither is a spacing decision. */
const PX_OK = new Set(["0", "1", "2"]);

function walk(dir) {
  const out = [];
  for (const entry of readdirSync(dir)) {
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) out.push(...walk(path));
    else if (/\.(tsx?|css)$/.test(path)) out.push(path);
  }
  return out;
}

const findings = [];
for (const root of ROOTS) {
  for (const file of walk(root)) {
    if (ALLOW_FILE.test(file)) continue;
    const source = readFileSync(file, "utf8");
    source.split("\n").forEach((line, index) => {
      // An SVG path is geometry, not layout.
      const isSvgGeometry = /viewBox|strokeWidth|strokeDasharray|\bd="/.test(line);
      const at = `${file}:${index + 1}`;

      for (const match of line.match(HEX) ?? []) {
        findings.push(`${at}  colour literal ${match} — use a token from globals.css`);
      }
      for (const match of line.match(FUNC_COLOUR) ?? []) {
        findings.push(`${at}  colour function ${match}…) — use a token from globals.css`);
      }
      if (isSvgGeometry) return;
      for (const match of line.matchAll(PX)) {
        if (PX_OK.has(match[1])) continue;
        findings.push(`${at}  raw ${match[0]} — use a --s-* step`);
      }
    });
  }
}

if (findings.length > 0) {
  console.error(`\n✗ ${findings.length} token violation(s):\n`);
  for (const finding of findings) console.error("  " + finding);
  console.error("\nThe design system is tokens. A literal here is a value nobody can restyle.\n");
  process.exit(1);
}
console.log("✓ no colour literals or raw spacing outside globals.css");
