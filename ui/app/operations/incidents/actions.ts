"use server";

import { redirect } from "next/navigation";
import { api, Refused } from "@/lib/api";
import type { Incident } from "@/lib/types";

/**
 * CF-V2-E12-04 — the three moves an operator can make on an incident, all
 * gated by ACKNOWLEDGE at the door.
 *
 * THE SERVER DECIDES. Nothing here inspects the incident's current state
 * before posting, or guesses whether a resolution string is good enough —
 * `core.operations.fingerprint.Incident`'s own transition methods do that,
 * and a browser re-implementing the same rule would be a second copy that
 * could drift. `ActionBar` only offers the buttons a state is LIKELY to
 * permit; this file, and the route behind it, are the actual answer.
 *
 * `redirect()` is called OUTSIDE the try/catch that renders a refusal — see
 * `deliverFile`'s own comment on the exact same trap: `next/navigation`
 * implements it by throwing, and a catch block written for "the server
 * refused" will happily catch the redirect instead.
 */

type Outcome = { outcome: string; headline: string };

async function move(
  incidentId: string,
  verb: "acknowledge" | "resolve" | "close",
  body: Record<string, string>,
): Promise<Outcome> {
  try {
    const incident = await api<Incident>(
      `/api/operations/incidents/${encodeURIComponent(incidentId)}/${verb}`,
      { method: "POST", body: JSON.stringify(body) },
    );
    return {
      outcome: incident.state.toUpperCase(),
      headline: `${incidentId} is now ${incident.state}.`,
    };
  } catch (error) {
    // A refusal is a DECISION the server made — an illegal transition, a
    // role without ACKNOWLEDGE, an empty resolution. It is rendered.
    // Anything else is a transport failure that reached nobody, and it is
    // re-thrown to the error boundary rather than dressed up as a decision
    // the platform did not make.
    if (!(error instanceof Refused)) throw error;
    return { outcome: "REFUSED", headline: error.detail };
  }
}

function back(incidentId: string, result: Outcome): string {
  const parameters = new URLSearchParams({ incident: incidentId, ...result });
  return `/operations/incidents?${parameters.toString()}`;
}

export async function acknowledgeIncident(formData: FormData): Promise<void> {
  const incidentId = String(formData.get("incident_id") ?? "");
  const assignedTo = String(formData.get("assigned_to") ?? "").trim();
  const result = await move(incidentId, "acknowledge", { assigned_to: assignedTo });
  redirect(back(incidentId, result));
}

export async function resolveIncident(formData: FormData): Promise<void> {
  const incidentId = String(formData.get("incident_id") ?? "");
  // Sent as typed, empty string included: the core's own refusal — "needs a
  // resolution that says what was done" — is the honest one, and it is what
  // the REFUSED banner below shows.
  const resolution = String(formData.get("resolution") ?? "");
  const result = await move(incidentId, "resolve", { resolution });
  redirect(back(incidentId, result));
}

export async function closeIncident(formData: FormData): Promise<void> {
  const incidentId = String(formData.get("incident_id") ?? "");
  const result = await move(incidentId, "close", {});
  redirect(back(incidentId, result));
}
