/** Route gate + the one place the access-token cookie gets refreshed.
 *
 * UX-level only - the real authorization boundary is the API (every
 * protected endpoint 401s on its own) and each page's own
 * `requireUser`/`requireRole` (lib/auth.ts). What this buys: an
 * unauthenticated request never renders a page it doesn't have data for, and
 * a near-expired access token gets silently renewed here rather than in
 * every Server Component that would otherwise need to mutate cookies mid-
 * render (which Next only allows in a Server Action or Route Handler -
 * middleware's response is exactly that kind of place).
 *
 * `looksExpired` decodes the JWT payload without verifying its signature -
 * that's fine, it only decides *whether to attempt* a refresh; the backend
 * is still the one authority that verifies every token it's handed.
 */

import { NextResponse, type NextRequest } from "next/server";

const ACCESS_COOKIE = "cinqflow_at";
const REFRESH_COOKIE = "cinqflow_rt";
const ACCESS_TTL_SECONDS = 60 * 15;
const REFRESH_TTL_SECONDS = 60 * 60 * 24 * 7;

const PUBLIC_PATHS = new Set(["/login"]);

function cookieOptions(maxAge: number) {
  return {
    httpOnly: true,
    sameSite: "lax" as const,
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge,
  };
}

function looksExpired(token: string): boolean {
  try {
    const payload = token.split(".")[1];
    const json = atob(payload.replace(/-/g, "+").replace(/_/g, "/"));
    const { exp } = JSON.parse(json) as { exp?: number };
    // 5s skew so a token that expires mid-request doesn't slip through.
    return typeof exp !== "number" || Date.now() >= exp * 1000 - 5000;
  } catch {
    return true;
  }
}

async function attemptRefresh(refreshToken: string): Promise<
  { access_token: string; refresh_token: string } | null
> {
  const base = process.env.CINQFLOW_API ?? "http://localhost:8000";
  try {
    const res = await fetch(`${base}/api/auth/refresh`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });
    if (!res.ok) return null;
    return (await res.json()) as { access_token: string; refresh_token: string };
  } catch {
    return null; // API unreachable - treat as "could not refresh", not a crash
  }
}

export async function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;
  if (PUBLIC_PATHS.has(pathname)) return NextResponse.next();

  const accessToken = request.cookies.get(ACCESS_COOKIE)?.value;
  if (accessToken && !looksExpired(accessToken)) return NextResponse.next();

  const refreshToken = request.cookies.get(REFRESH_COOKIE)?.value;
  if (refreshToken) {
    const refreshed = await attemptRefresh(refreshToken);
    if (refreshed) {
      const response = NextResponse.next();
      response.cookies.set(ACCESS_COOKIE, refreshed.access_token, cookieOptions(ACCESS_TTL_SECONDS));
      response.cookies.set(REFRESH_COOKIE, refreshed.refresh_token, cookieOptions(REFRESH_TTL_SECONDS));
      return response;
    }
  }

  const loginUrl = new URL("/login", request.url);
  loginUrl.searchParams.set("next", pathname);
  const response = NextResponse.redirect(loginUrl);
  response.cookies.delete(ACCESS_COOKIE);
  response.cookies.delete(REFRESH_COOKIE);
  return response;
}

export const config = {
  // Everything except Next's own internals and static files - a route added
  // later is gated by default rather than accidentally left open.
  matcher: ["/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)"],
};
