import type { NextConfig } from "next";

// The UI holds no secrets and no business rules. It renders what the BFF says,
// and the BFF is the only thing that decides what a caller may see — which is
// why a crafted edit URL is denied at the server, not hidden in this menu.
const config: NextConfig = {
  reactStrictMode: true,
  // The test server gets its OWN build directory.
  //
  // `npm test` starts a second `next dev` beside the one somebody is browsing,
  // and both wrote to `.next`. Two dev servers rebuilding one directory
  // corrupt each other's client-reference manifest, and the symptom is not a
  // build error — it is the running app serving a 500 for a page that is
  // fine, or a sign-in that silently fails to set its cookie. Anyone who ran
  // the suite while the app was open then went looking for a bug in the page.
  distDir: process.env.NEXT_DIST_DIR ?? ".next",
  env: {
    CINQFLOW_API: process.env.CINQFLOW_API ?? "http://127.0.0.1:8000",
  },
  // `deliverFile` (app/data/intake/deliver/actions.ts) posts the raw file
  // straight to this Server Action, and Next enforces its OWN transport-level
  // cap here BEFORE the platform ever sees the bytes — a 1 MB default that has
  // nothing to do with `core.landing._size_bounds`, the feed's real,
  // per-feed-configured limit (30 MB for the Fidelis golden feed). Real
  // enrollment/claims rosters routinely run tens of MB; left at the default,
  // Next silently refuses a delivery the platform's own rule would have
  // accepted, which is exactly the "browser judges bytes the platform never
  // saw" case `deliverFile`'s docstring says cannot happen.
  experimental: {
    serverActions: {
      bodySizeLimit: "100mb",
    },
  },
};

export default config;
