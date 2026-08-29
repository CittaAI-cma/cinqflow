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
  reporter: [["list"]],
  use: {
    baseURL: "http://127.0.0.1:3100",
    trace: "retain-on-failure",
  },
  webServer: [
    {
      command: "../.venv/bin/python -m cinqflow.api.dev --port 8100",
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
