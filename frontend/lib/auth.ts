/** Server-only session handling. The browser never sees a bearer token - only
 *  Next.js does, held as two httpOnly cookies on its own origin. The API never
 *  sets cookies itself; this module is the one place that translates its JSON
 *  token pairs into cookies and back. See
 *  docs/blueprints/auth-and-user-management.md §3 for why.
 *
 *  `middleware.ts` is what keeps the access-token cookie fresh (it runs on
 *  every request and can rewrite the response's cookies); everything here
 *  reads it as already-current, so `getCurrentUser` never mutates cookies and
 *  is safe to call from a plain Server Component, not just a Server Action. */

import { cookies } from "next/headers";
import { redirect } from "next/navigation";

const API_BASE = process.env.CINQFLOW_API ?? "http://localhost:8000";

export const ACCESS_COOKIE = "cinqflow_at";
export const REFRESH_COOKIE = "cinqflow_rt";

const ACCESS_TTL_SECONDS = 60 * 15;
const REFRESH_TTL_SECONDS = 60 * 60 * 24 * 7;

/** Mirrors `backend/src/cinqflow/auth/persona.py` - derived there from roles,
 *  never re-derived here. Persona is emphasis (defaults, see `lib/persona.ts`);
 *  capabilities are authority (the API enforces them; the UI only mirrors). */
export type Persona = "data_analyst" | "data_platform";

export interface Capabilities {
  can_decide_gates: boolean;
  can_rerun_steps: boolean;
  can_manage_users: boolean;
}

export interface CurrentUser {
  id: string;
  email: string;
  display_name: string;
  roles: string[];
  persona: Persona;
  capabilities: Capabilities;
}

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: string;
  user: CurrentUser;
}

function cookieOptions(maxAge: number) {
  return {
    httpOnly: true,
    sameSite: "lax" as const,
    // Same-origin to the browser either way (Next.js is the only thing that
    // ever holds the token) - `secure` still needs to be off for plain-http
    // local dev, same as every other "obviously fine over http" cookie.
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge,
  };
}

export async function persistSession(tokens: TokenPair): Promise<void> {
  const store = await cookies();
  store.set(ACCESS_COOKIE, tokens.access_token, cookieOptions(ACCESS_TTL_SECONDS));
  store.set(REFRESH_COOKIE, tokens.refresh_token, cookieOptions(REFRESH_TTL_SECONDS));
}

export async function clearSession(): Promise<void> {
  const store = await cookies();
  store.delete(ACCESS_COOKIE);
  store.delete(REFRESH_COOKIE);
}

/** The `login` Server Action's one call to the backend. Returns a message
 *  instead of throwing, so the form can show it - same shape as
 *  `lib/api.ts`'s `uploadFile`/`createMappingVersion`. */
export async function login(email: string, password: string): Promise<{ error?: string }> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}/api/auth/login`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ email, password }),
      cache: "no-store",
    });
  } catch {
    // Same unguarded-fetch bug as `getCurrentUser` had, and it matters most
    // here: a page behind `requireUser`/`requireRole` redirects to /login when
    // the API is down, so this form is exactly where someone lands - and
    // "Invalid email or password" would send them hunting for a credential
    // problem that does not exist.
    return { error: "Can't reach the sign-in service. The API may be down — your credentials are fine." };
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    if (body?.detail === "account_deactivated") {
      return { error: "This account has been deactivated. Contact an administrator." };
    }
    return { error: "Invalid email or password." };
  }
  await persistSession((await res.json()) as TokenPair);
  return {};
}

/** Render-safe: never mutates cookies, so a Server Component can call it
 *  directly. An expired/missing/invalid token reads the same as "signed
 *  out" - middleware is what redirects, this just reports the current state. */
export async function getCurrentUser(): Promise<CurrentUser | null> {
  const store = await cookies();
  const token = store.get(ACCESS_COOKIE)?.value;
  if (!token) return null;
  let res: Response;
  try {
    res = await fetch(`${API_BASE}/api/auth/me`, {
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store",
    });
  } catch {
    // The API is unreachable. `fetch` *throws* on a refused connection rather
    // than returning a non-ok response, and the root layout calls this on
    // every render - so letting it propagate takes down the entire tree and
    // renders a blank page, which is also why every page's own
    // API-unreachable handling never got a chance to run. Middleware already
    // set this precedent (`attemptRefresh`: "API unreachable - treat as
    // could not refresh, not a crash"); this is the same call.
    //
    // `null` reads as "signed out" in the shell, which is imprecise - but the
    // page below now renders `ApiUnreachable`, which names the real cause,
    // and an imprecise chrome beats a white screen.
    return null;
  }
  if (!res.ok) return null;
  return (await res.json()) as CurrentUser;
}

/** For a Server Component that must not render without a signed-in user -
 *  middleware already redirects unauthenticated requests away from every
 *  non-/login route, so reaching here with no user is the rare edge (a race
 *  with an access token that expired between middleware and render). */
export async function requireUser(): Promise<CurrentUser> {
  const user = await getCurrentUser();
  if (!user) redirect("/login");
  return user;
}

export async function requireRole(role: string): Promise<CurrentUser> {
  const user = await requireUser();
  if (!user.roles.includes(role)) redirect("/");
  return user;
}

/** Authenticated GET from a Server Component or Action. Throws on failure,
 *  matching `lib/api.ts`'s own `get<T>`. */
export async function authGet<T>(path: string): Promise<T> {
  const store = await cookies();
  const token = store.get(ACCESS_COOKIE)?.value;
  const res = await fetch(`${API_BASE}${path}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`${path} returned ${res.status}`);
  return res.json() as Promise<T>;
}

/** Authenticated mutation from a Server Action. `{data?, error?}` instead of
 *  throwing, matching `lib/api.ts`'s `uploadFile`/`createMappingVersion`/etc. */
export async function authMutate<T>(
  path: string,
  init: RequestInit,
): Promise<{ data?: T; error?: string }> {
  const store = await cookies();
  const token = store.get(ACCESS_COOKIE)?.value;
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "content-type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...init.headers,
    },
    cache: "no-store",
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    return { error: humanizeError(body?.detail, res.status) };
  }
  return { data: body as T };
}

/** The API's two error shapes, in the analyst's words. A string `detail` is an
 *  auth/admin code; an object `detail` is a control-plane 409/404 carrying
 *  `message` and often a `hint` the spec says to render verbatim. */
function humanizeError(detail: unknown, status: number): string {
  if (typeof detail === "string") return humanizeAuthError(detail);
  if (detail && typeof detail === "object" && "message" in detail) {
    const d = detail as { message: string; hint?: string; status?: string };
    if (d.hint) return `${d.message} — ${d.hint}`;
    if (d.status) return `${d.message} (status: ${d.status})`;
    return d.message;
  }
  return `request failed (${status})`;
}

function humanizeAuthError(detail: string): string {
  if (detail === "email_already_exists") return "A user with this email already exists.";
  if (detail.startsWith("unknown_role")) return "One of the selected roles doesn't exist.";
  if (detail === "missing_role:administrator") return "Administrator access is required.";
  if (detail === "missing_capability:can_decide_gates") {
    return "Your role can review this run but not decide it — an approver or business analyst signs the gate.";
  }
  if (detail === "missing_capability:can_rerun_steps") {
    return "Retrying and re-running are Data Platform actions — a data engineer or operations role.";
  }
  if (
    detail === "not_authenticated" ||
    detail === "invalid_token" ||
    detail === "user_not_found_or_inactive"
  ) {
    return "Your session has ended — sign in again.";
  }
  if (detail === "unknown_user") return "That user no longer exists.";
  return detail;
}
