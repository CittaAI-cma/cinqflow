/** Branding. Single place to change what the shell calls itself. Who it greets
 *  is a fetched profile now (lib/auth.ts's `getCurrentUser`/`requireUser`), not
 *  configuration — see docs/blueprints/auth-and-user-management.md. */

// `||`, not `??`: docker-compose passes `${VAR:-}` for an unset var, which
// Next.js reads as `""`, not `undefined` - `??` would let a blank string
// through as "the configured value" instead of falling back. See
// compose/docker-compose.yml's frontend service and Dockerfile.railway for
// where these are actually wired for local dev and Railway respectively.
export const BRAND_NAME = process.env.NEXT_PUBLIC_BRAND_NAME || "Digitalurth";

/** The deployment this console is pointed at. Shown per row on the ingestion
 *  register, which is why every row carries the same value. */
export const PLATFORM_ENVIRONMENT =
  process.env.NEXT_PUBLIC_ENVIRONMENT || "dl-dev-environment";

export const PLATFORM_PROJECT = process.env.NEXT_PUBLIC_PROJECT || "dl-dev-project";

/** Seed values for the source-connection picker; anything already ingested is
 *  merged in alongside these, and a new one can be typed. */
export const SOURCE_SYSTEMS = ["fidelis_ny_upstate", "fidelis_ny_downstate"] as const;

/** Seed values for the Data domain picker — the domains this platform has
 *  governed knowledge for (`knowledge/domains/*.yaml`), so a fresh deployment
 *  with no uploads yet still offers real choices instead of an empty list.
 *  Anything already ingested is merged in alongside these (same pattern as
 *  SOURCE_SYSTEMS), and a new one can still be typed.
 *
 *  Keep in sync with `knowledge/domains/`. `clinical`/`quality`/`risk` have
 *  domain knowledge but no `knowledge/canonical/*.yaml` yet, so a feed picking
 *  one of those can be profiled and interpreted but won't have a canonical
 *  target to map to until that catalog gains an entry — worth knowing, not a
 *  reason to hide the option. */
export const DATA_DOMAINS = [
  "enrollment",
  "claims",
  "adt",
  "provider",
  "clinical",
  "quality",
  "risk",
] as const;

export const DEFAULT_UPLOADER = process.env.NEXT_PUBLIC_UPLOADER || "analyst@cinqcare.com";
