import { defineConfig } from "@playwright/test";

/**
 * The UI half of the twin end-to-end run.
 *
 * Both servers are started here so `npm test` is one command and CI has no
 * bespoke orchestration. The API runs on the mock socket — rung 0, nothing
 * running but Python — because these tests prove the UI, not the data plane.
 */
export default defineConfig({
  testDir: "./tests",
  timeout: 30_000,
  fullyParallel: false,
  // One worker, not just one test at a time. `fullyParallel: false` serialises
  // tests WITHIN a file; separate spec files still run across workers, and
  // every one of them shares a single stateful API — so an a11y sweep of
  // /ai/observability could analyse the page while another file was writing
  // agent-action rows into it. The suite exercises one control plane; it has
  // to be one writer.
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: "http://127.0.0.1:3100",
    trace: "retain-on-failure",
  },
  webServer: [
    {
      // Relative to `cwd` below (cinqflow/), NOT to this file. It was
      // "../.venv/…", which resolves to the repo root and does not exist —
      // invisible for as long as a server happened to already be running and
      // `reuseExistingServer` skipped the spawn.
      command: ".venv/bin/python -m cinqflow.api.dev --port 8100",
      url: "http://127.0.0.1:8100/healthz",
      reuseExistingServer: !process.env.CI,
      cwd: "..",
      env: { PYTHONPATH: "src" },
    },
    {
      command: "npx next dev -p 3100",
      url: "http://127.0.0.1:3100/signin",
      reuseExistingServer: !process.env.CI,
      env: { CINQFLOW_API: "http://127.0.0.1:8100" },
    },
  ],
});
