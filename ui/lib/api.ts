import { cookies } from "next/headers";

// The ONLY module that talks to the BFF. Two consequences worth the
// indirection: the session token is read in one place, and a 403 is rendered as
// a refusal rather than thrown as a crash — because "you may not do that" is an
// ANSWER, and an application that treats it as an error hands the user a stack
// trace instead of a sentence.

const API = process.env.CINQFLOW_API ?? "http://127.0.0.1:8000";

export const SESSION_COOKIE = "cinqflow_session";

export class Refused extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
  ) {
    super(detail);
  }
}

export async function token(): Promise<string | null> {
  const jar = await cookies();
  return jar.get(SESSION_COOKIE)?.value ?? null;
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const bearer = await token();
  const response = await fetch(`${API}${path}`, {
    ...init,
    headers: {
      "content-type": "application/json",
      ...(bearer ? { authorization: `Bearer ${bearer}` } : {}),
      ...(init?.headers ?? {}),
    },
    // The control plane changes while you are looking at it. A cached batch
    // state is a screen that lies quietly.
    cache: "no-store",
  });

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* a non-JSON error body is still an error */
    }
    throw new Refused(response.status, detail);
  }
  return (await response.json()) as T;
}

/** Fetch, or return the refusal to RENDER. Never swallows it. */
export async function attempt<T>(path: string, init?: RequestInit): Promise<T | Refused> {
  try {
    return await api<T>(path, init);
  } catch (error) {
    if (error instanceof Refused) return error;
    throw error;
  }
}

export function isRefused<T>(value: T | Refused): value is Refused {
  return value instanceof Refused;
}
