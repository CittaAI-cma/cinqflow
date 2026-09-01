"use server";

import { redirect } from "next/navigation";
import { api, Refused } from "@/lib/api";
import type { ActionRecord } from "@/lib/types";

/**
 * CF-V2-E12-03 / CF-V2-E8-04 — the governed action surface's one door.
 *
 * ONE server action for all ten `OpsAction`s, dispatched by a hidden
 * `ops_action` field each form carries — the same reason the surface itself
 * is one route rather than ten: a tenth action arriving is a row in a table,
 * not a new file. `ActionSurfacePanel` decides which buttons and fields to
 * offer; this decides nothing about legality at all. `authorize` on the wire
 * is the only place that does, and every branch below either renders its
 * answer or lets a genuine transport failure reach the error boundary.
 *
 * `redirect()` is called OUTSIDE the try/catch, same trap `incidents/actions
 * .ts` documents: `next/navigation` implements it by throwing, and a catch
 * block written for "the server refused" would happily catch the redirect.
 */

type Outcome = { outcome: string; headline: string };

async function act(batchId: string, body: Record<string, unknown>): Promise<Outcome> {
  const action = String(body.action ?? "");
  try {
    const record = await api<ActionRecord>(
      `/api/operations/batches/${encodeURIComponent(batchId)}/actions`,
      { method: "POST", body: JSON.stringify(body) },
    );
    const headline = record.is_complete
      ? `${action} on ${batchId}: ${record.outcome || record.phase}.`
      : `${action} requested on ${batchId} — not yet verified.`;
    return { outcome: record.phase.toUpperCase(), headline };
  } catch (error) {
    // A refusal is a DECISION the server made — wrong state, no reason, the
    // feed is paused, a missing approval identifier. It is rendered, not
    // thrown: `authorize`'s whole point is that every refusal leaves a row
    // and reaches the person who asked, in their own words.
    if (!(error instanceof Refused)) throw error;
    return { outcome: "REFUSED", headline: error.detail };
  }
}

function back(batchId: string, panel: string, result: Outcome): string {
  const parameters = new URLSearchParams({ panel, ...result });
  return `/operations/control/batch/${encodeURIComponent(batchId)}?${parameters.toString()}`;
}

export async function postBatchAction(formData: FormData): Promise<void> {
  const batchId = String(formData.get("batch_id") ?? "");
  const panel = String(formData.get("panel") ?? "recon");

  const body: Record<string, unknown> = {
    action: String(formData.get("ops_action") ?? ""),
    reason: String(formData.get("reason") ?? ""),
    approval_identifier: String(formData.get("approval_identifier") ?? ""),
  };
  const assignee = String(formData.get("assignee") ?? "").trim();
  if (assignee) body.assignee = assignee;
  const note = String(formData.get("note") ?? "").trim();
  if (note) body.note = note;
  const resumeFrom = String(formData.get("resume_from") ?? "");
  if (resumeFrom) body.resume_from = resumeFrom;
  const businessDate = String(formData.get("business_date") ?? "");
  if (businessDate) body.business_date = businessDate;
  if (formData.get("supersede_acknowledged")) body.supersede_acknowledged = true;

  const result = await act(batchId, body);
  redirect(back(batchId, panel, result));
}
