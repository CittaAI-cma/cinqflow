"use server";

import { redirect } from "next/navigation";
import { api, Refused } from "@/lib/api";
import type { IdentityException } from "@/lib/types";

/**
 * CF-V3-E9-02 — the two moves a steward can make on an identity exception,
 * gated by ASSIGN and ACKNOWLEDGE respectively at the door.
 *
 * THE SERVER DECIDES. Nothing here inspects the exception's current state
 * before posting — `core.identity.exceptions.assign`/`resolve` do that, and
 * a browser re-implementing the same rule would be a second copy that could
 * drift. See `operations/incidents/actions.ts` for the identical reasoning
 * this file is deliberately shaped after.
 *
 * `redirect()` is called OUTSIDE the try/catch that renders a refusal —
 * `next/navigation` implements it by throwing, and a catch written for "the
 * server refused" will happily catch the redirect instead.
 */

type Outcome = { outcome: string; headline: string };

async function move(
  key: string,
  verb: "assign" | "resolve",
  body: Record<string, string>,
): Promise<Outcome> {
  try {
    const exception = await api<IdentityException>(
      `/api/identity/exceptions/${encodeURIComponent(key)}/${verb}`,
      { method: "POST", body: JSON.stringify(body) },
    );
    return {
      outcome: exception.state.toUpperCase(),
      headline: `${key} is now ${exception.state}.`,
    };
  } catch (error) {
    if (!(error instanceof Refused)) throw error;
    return { outcome: "REFUSED", headline: error.detail };
  }
}

function back(result: Outcome): string {
  const parameters = new URLSearchParams(result);
  return `/data/identity/queue?${parameters.toString()}`;
}

export async function assignException(formData: FormData): Promise<void> {
  const key = String(formData.get("key") ?? "");
  const assignedTo = String(formData.get("assigned_to") ?? "").trim();
  const result = await move(key, "assign", { assigned_to: assignedTo });
  redirect(back(result));
}

export async function resolveException(formData: FormData): Promise<void> {
  const key = String(formData.get("key") ?? "");
  const note = String(formData.get("note") ?? "").trim();
  const result = await move(key, "resolve", { note });
  redirect(back(result));
}
