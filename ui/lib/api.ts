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

/**
 * The platform is not running, or not where we looked.
 *
 * Distinct from `Refused`, and the distinction is the whole point: a refusal
 * is a DECISION the server made and recorded, while this is a transport
 * failure that reached nobody. Rendering them the same way sends someone
 * hunting for a permission bug when the API simply is not up.
 *
 * Node's fetch throws a bare `TypeError: fetch failed` with the URL only in a
 * nested `cause`, so the message a person actually reads says nothing about
 * where we looked or how to fix it. This says both.
 */
export class Unreachable extends Error {
  constructor(
    readonly url: string,
    options?: { cause?: unknown },
  ) {
    super(
      `Could not reach the CINQFLOW API at ${url}. ` +
        `Start it with:  cd cinqflow && PYTHONPATH=src .venv/bin/python -m cinqflow.api.dev --port 8000  ` +
        `— or point CINQFLOW_API at wherever it is running.`,
      options,
    );
    this.name = "Unreachable";
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const bearer = await token();
  const url = `${API}${path}`;

  let response: Response;
  try {
    response = await fetch(url, {
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
  } catch (cause) {
    throw new Unreachable(url, { cause });
  }

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

/**
 * A multipart POST. CF-V1-E3-05 — the one request that is not JSON.
 *
 * Separate from `api()` rather than a flag on it, because the two differ in a
 * way a flag would hide: `content-type` must be ABSENT here so the runtime can
 * set it with the multipart boundary it generated. Setting it by hand — which
 * `api()` does, correctly, for every other call — produces a request the
 * server cannot parse, with an error that names the boundary and not the
 * cause.
 */
export async function upload<T>(path: string, form: FormData): Promise<T> {
  const bearer = await token();
  const url = `${API}${path}`;

  let response: Response;
  try {
    response = await fetch(url, {
      method: "POST",
      body: form,
      headers: bearer ? { authorization: `Bearer ${bearer}` } : {},
      cache: "no-store",
    });
  } catch (cause) {
    throw new Unreachable(url, { cause });
  }

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
