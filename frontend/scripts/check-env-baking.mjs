#!/usr/bin/env node
// Regression guard for a real incident: next.config.ts used to declare
//   env: { CINQFLOW_API: process.env.CINQFLOW_API ?? "http://localhost:8000" }
// which is Next.js's BUILD-TIME string-substitution mechanism (same one
// NEXT_PUBLIC_* uses) - not a runtime passthrough. Since Dockerfile.railway
// never passes CINQFLOW_API as a build ARG (only NEXT_PUBLIC_CINQFLOW_API
// is), every image baked in the http://localhost:8000 fallback permanently,
// no matter what the Railway variable was set to at runtime. The result was
// a server-side ECONNREFUSED on every request, diagnosed only after adding
// console.error logging to two swallowed catch blocks and reading Railway's
// deploy logs - nothing in the browser ever showed a clue.
//
// This script builds the app with two distinct canary values and greps the
// compiled output for them, asserting each var baked into the *right* half:
//   - CINQFLOW_API (server-only) must NOT appear anywhere under .next/server
//     as a literal - it must stay a live `process.env` read at request time.
//   - NEXT_PUBLIC_CINQFLOW_API (client-visible) MUST appear under
//     .next/static - that one is supposed to bake in at build time.
//
// No test framework required (none is configured for this project yet);
// this is a plain, dependency-free Node script wired as `pnpm run check-env-baking`.

import { execSync } from "node:child_process";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

const SERVER_CANARY = "http://server-side-canary-should-not-be-baked:9001";
const CLIENT_CANARY = "http://client-side-canary-must-be-baked:9002";

function walk(dir) {
  const files = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    const stat = statSync(full);
    if (stat.isDirectory()) files.push(...walk(full));
    else if (/\.(js|html|json)$/.test(entry)) files.push(full);
  }
  return files;
}

function containsCanary(dir, canary) {
  for (const file of walk(dir)) {
    if (readFileSync(file, "utf8").includes(canary)) return file;
  }
  return null;
}

console.log("Building with canary env values (this takes a minute)...");
execSync("pnpm exec next build", {
  cwd: new URL("..", import.meta.url),
  stdio: "inherit",
  env: {
    ...process.env,
    CINQFLOW_API: SERVER_CANARY,
    NEXT_PUBLIC_CINQFLOW_API: CLIENT_CANARY,
  },
});

const root = new URL("..", import.meta.url).pathname;
let failed = false;

const serverHit = containsCanary(join(root, ".next", "server"), SERVER_CANARY);
if (serverHit) {
  failed = true;
  console.error(
    `FAIL: CINQFLOW_API (server-only) got baked into the build at build ` +
      `time - found in ${serverHit}. It must be read live via ` +
      `process.env.CINQFLOW_API at request time, not compiled in ` +
      `(check next.config.ts for a stray \`env: { CINQFLOW_API: ... }\` block).`,
  );
} else {
  console.log("OK: CINQFLOW_API is not baked into .next/server (read live at request time).");
}

const clientHit = containsCanary(join(root, ".next", "static"), CLIENT_CANARY);
if (!clientHit) {
  failed = true;
  console.error(
    `FAIL: NEXT_PUBLIC_CINQFLOW_API did not get baked into .next/static - ` +
      `the browser bundle would fall back to its default at runtime instead ` +
      `of using the configured API URL.`,
  );
} else {
  console.log("OK: NEXT_PUBLIC_CINQFLOW_API is baked into the client bundle as expected.");
}

process.exit(failed ? 1 : 0);
