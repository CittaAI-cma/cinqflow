/** Branding and identity. Single place to change what the shell calls itself and
 *  who it greets — there is no auth or user API on this build, so the display
 *  name is configuration rather than a fetched profile. */

export const BRAND_NAME = process.env.NEXT_PUBLIC_BRAND_NAME ?? "Digitalurth";

export const USER_DISPLAY_NAME = process.env.NEXT_PUBLIC_USER_NAME ?? "Ana";

/** Initials for the account chip in the top bar. */
export const USER_INITIALS = process.env.NEXT_PUBLIC_USER_INITIALS ?? "MR";

/** The deployment this console is pointed at. Shown per row on the ingestion
 *  register, which is why every row carries the same value. */
export const PLATFORM_ENVIRONMENT =
  process.env.NEXT_PUBLIC_ENVIRONMENT ?? "dl-dev-environment";

export const PLATFORM_PROJECT = process.env.NEXT_PUBLIC_PROJECT ?? "dl-dev-project";

/** Seed values for the source-connection picker; anything already ingested is
 *  merged in alongside these, and a new one can be typed. */
export const SOURCE_SYSTEMS = ["fidelis_ny_upstate", "fidelis_ny_downstate"] as const;

export const DEFAULT_UPLOADER = process.env.NEXT_PUBLIC_UPLOADER ?? "analyst@cinqcare.com";
