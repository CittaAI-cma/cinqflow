"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { api, Refused } from "@/lib/api";

/**
 * W1-31 (CF-V1-E6-03) — the write half of proposal review, real for the
 * first time. Five acts, three doors:
 *
 *   - `acceptProposal` / `rejectProposal` post to the PROPOSAL
 *     (`POST /api/proposals/{id}/approve|reject`) — EDIT_FEED, because
 *     accepting an agent's draft is AUTHORING (see that route's own
 *     docstring). Generic across every agent: a mapping proposal's form
 *     carries `source_column` rows and this reads them into `mappings`; any
 *     other agent's form carries none, and an empty `mappings` accepts the
 *     proposal exactly as written.
 *   - `submitForReview` / `approveObject` / `publishObject` post to the
 *     GOVERNED OBJECT the acceptance produced (`POST
 *     /api/objects/{type}/{id}/submit|approve|publish`) — SUBMIT_FOR_REVIEW,
 *     APPROVE and PUBLISH respectively, which is why a mapping's own
 *     lifecycle needs a different signed-in person at each step from the one
 *     who accepted the proposal.
 *
 * THE SERVER DECIDES. Nothing here inspects a role, a lifecycle state or a
 * diff before posting — the four routes behind these forms are the actual
 * authority, exactly as `ActionBar`'s own docstring says of the incidents
 * screen. This file only shapes the FormData into the body each route
 * expects and turns its answer into a banner.
 *
 * `redirect()` is called OUTSIDE every try/catch, for the reason
 * `deliverFile`'s own comment gives: `next/navigation` implements it by
 * THROWING, and a catch written for "the server refused" will happily catch
 * the redirect instead.
 */

type Outcome = { outcome: string; headline: string };

function back(proposalId: string, result: Outcome): string {
  const query = new URLSearchParams({ outcome: result.outcome, headline: result.headline });
  return `/data/intake/proposals/${encodeURIComponent(proposalId)}?${query.toString()}`;
}

/**
 * One `MappingDecisionIn` per source column the form carried — which, for a
 * mapping proposal, is every line: absent fields would keep the agent's own
 * value (`_accepted_mapping_records`'s own rule), but a plain form has no way
 * to omit a field it does not know changed, so every row's CURRENT value is
 * sent. Sending back an unchanged value produces no correction — `diff_fields`
 * compares before and after, not "was a field present".
 */
function mappingDecisions(formData: FormData): Record<string, unknown>[] {
  const sourceColumns = formData.getAll("source_column").map(String);
  return sourceColumns.map((sourceColumn, index) => ({
    source_column: sourceColumn,
    target_entity: String(formData.get(`target_entity_${index}`) ?? "").trim() || null,
    target_field: String(formData.get(`target_field_${index}`) ?? "").trim() || null,
    unmapped: formData.get(`unmapped_${index}`) != null,
    unmapped_reason: String(formData.get(`unmapped_reason_${index}`) ?? "").trim() || null,
  }));
}

export async function acceptProposal(formData: FormData): Promise<void> {
  const proposalId = String(formData.get("proposal_id") ?? "");
  const comment = String(formData.get("comment") ?? "");
  const mappings = mappingDecisions(formData);

  let result: Outcome;
  try {
    const proposal = await api<{ state: string; applied_object_type: string | null }>(
      `/api/proposals/${encodeURIComponent(proposalId)}/approve`,
      { method: "POST", body: JSON.stringify({ comment, mappings }) },
    );
    revalidatePath(`/data/intake/proposals/${proposalId}`);
    revalidatePath("/data/intake/proposals");
    result = {
      outcome: "ACCEPTED",
      headline: proposal.applied_object_type
        ? `Accepted. A draft ${proposal.applied_object_type} is now waiting on its own review.`
        : `Accepted — now ${proposal.state.replace(/_/g, " ")}.`,
    };
  } catch (error) {
    // A refusal is a decision the server made and recorded; it is rendered.
    // Anything else reached nobody and is re-thrown to the error boundary.
    if (!(error instanceof Refused)) throw error;
    result = { outcome: "REFUSED", headline: error.detail };
  }
  redirect(back(proposalId, result));
}

export async function rejectProposal(formData: FormData): Promise<void> {
  const proposalId = String(formData.get("proposal_id") ?? "");
  // Sent as typed, empty string included: `proposals.reject`'s own refusal —
  // "a rejection needs a reason" — is the honest one, shown in the REFUSED
  // banner rather than pre-empted here.
  const comment = String(formData.get("comment") ?? "");

  let result: Outcome;
  try {
    await api(`/api/proposals/${encodeURIComponent(proposalId)}/reject`, {
      method: "POST",
      body: JSON.stringify({ comment }),
    });
    revalidatePath(`/data/intake/proposals/${proposalId}`);
    revalidatePath("/data/intake/proposals");
    result = { outcome: "REJECTED", headline: "Rejected. Nothing was applied." };
  } catch (error) {
    if (!(error instanceof Refused)) throw error;
    result = { outcome: "REFUSED", headline: error.detail };
  }
  redirect(back(proposalId, result));
}

export async function submitForReview(formData: FormData): Promise<void> {
  const proposalId = String(formData.get("proposal_id") ?? "");
  const objectType = String(formData.get("object_type") ?? "");
  const objectId = String(formData.get("object_id") ?? "");
  const comment = String(formData.get("comment") ?? "");

  let result: Outcome;
  try {
    const object = await api<{ lifecycle_state: string }>(
      `/api/objects/${encodeURIComponent(objectType)}/${encodeURIComponent(objectId)}/submit`,
      { method: "POST", body: JSON.stringify({ comment }) },
    );
    revalidatePath(`/data/intake/proposals/${proposalId}`);
    result = {
      outcome: "SUBMITTED",
      headline: `Now ${object.lifecycle_state.replace(/_/g, " ")}, waiting on an approver.`,
    };
  } catch (error) {
    if (!(error instanceof Refused)) throw error;
    result = { outcome: "REFUSED", headline: error.detail };
  }
  redirect(back(proposalId, result));
}

export async function approveObject(formData: FormData): Promise<void> {
  const proposalId = String(formData.get("proposal_id") ?? "");
  const objectType = String(formData.get("object_type") ?? "");
  const objectId = String(formData.get("object_id") ?? "");
  const comment = String(formData.get("comment") ?? "");
  // CF-V1-E6-04. The NAMES the approver checked, not a plain "yes" — the
  // route refuses to publish a loss nobody named (`TransitionIn.accepts_loss`).
  const acceptsLoss = formData.getAll("accepts_loss").map(String);

  let result: Outcome;
  try {
    const object = await api<{ lifecycle_state: string }>(
      `/api/objects/${encodeURIComponent(objectType)}/${encodeURIComponent(objectId)}/approve`,
      { method: "POST", body: JSON.stringify({ comment, accepts_loss: acceptsLoss }) },
    );
    revalidatePath(`/data/intake/proposals/${proposalId}`);
    result = { outcome: "APPROVED", headline: `Now ${object.lifecycle_state}, ready to publish.` };
  } catch (error) {
    if (!(error instanceof Refused)) throw error;
    result = { outcome: "REFUSED", headline: error.detail };
  }
  redirect(back(proposalId, result));
}

export async function publishObject(formData: FormData): Promise<void> {
  const proposalId = String(formData.get("proposal_id") ?? "");
  const objectType = String(formData.get("object_type") ?? "");
  const objectId = String(formData.get("object_id") ?? "");

  let result: Outcome;
  try {
    const object = await api<{ lifecycle_state: string }>(
      `/api/objects/${encodeURIComponent(objectType)}/${encodeURIComponent(objectId)}/publish`,
      { method: "POST", body: JSON.stringify({ comment: "" }) },
    );
    revalidatePath(`/data/intake/proposals/${proposalId}`);
    result = { outcome: "PUBLISHED", headline: `${objectType} ${objectId} is now live.` };
  } catch (error) {
    if (!(error instanceof Refused)) throw error;
    result = { outcome: "REFUSED", headline: error.detail };
  }
  redirect(back(proposalId, result));
}
