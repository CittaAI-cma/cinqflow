import type { NextConfig } from "next";

// Deliberately no `env: { CINQFLOW_API: ... }` here: Next.js's `env` config
// key is a BUILD-TIME string substitution (same mechanism as NEXT_PUBLIC_,
// just without the prefix) - it does not pass a runtime value through.
// CINQFLOW_API needs to differ per deploy target (Docker Compose's
// in-network hostname, Railway's private-network hostname, ...) and is only
// ever read server-side (see lib/api.ts's `typeof window === "undefined"`
// branch), so plain `process.env.CINQFLOW_API` already does the right
// thing at request time with zero config - Node reads live container env,
// no build step involved. Putting it in `env` here previously baked in
// whatever CINQFLOW_API happened to be during `pnpm build` (nothing, in
// Dockerfile.railway - only NEXT_PUBLIC_CINQFLOW_API is passed as a build
// ARG there), permanently overriding the runtime value on every deploy.
const nextConfig: NextConfig = {
  experimental: {
    serverActions: {
      bodySizeLimit: "50mb",
    },
    // `middleware.ts` runs on every route except /login (the session-cookie
    // gate), /data/intake/new included - middleware has its own, separate
    // body-size cap from the Server Action it fronts, and its own default is
    // much smaller (~1MB, surfaced as Next silently truncating the request at
    // ~10MB in practice and the Server Action then failing to parse the cut-
    // off multipart body: "Error: Unexpected end of form"). A real ingestion
    // file routinely exceeds that, so this has to match serverActions' limit
    // or the smaller of the two silently wins.
    middlewareClientMaxBodySize: "50mb",
  },
};

export default nextConfig;
