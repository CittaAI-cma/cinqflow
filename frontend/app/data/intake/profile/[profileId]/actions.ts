"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { Refused, api } from "@/lib/api";

/**
 * CF-V1-E5-02 · E5-03 · E6-02 · E7-01 — the four AI capabilities, given a door.
 *
 * ALL FOUR WERE REACHABLE ONLY BY `curl`. `POST /infer-schema`,
 * `/detect-phi`, `/suggest-mapping` and `/author-rules` are routed, fitted and
 * tested, and a scan of every `fetch` in this application found no caller for
 * any of them. The agents ran; nobody in a browser could ask them to.
 *
 * THE PROPOSAL IS THE DESTINATION, NOT A TOAST. Every one of these returns a
 * `Proposal` — a draft a human approves — so the action redirects to the
 * review screen for it. "Publish anything — its output is always a draft for
 * the BA and steward" is CF-V1-E6-02's own don't, and landing the user on the
 * thing they must now review is what makes that true in the product rather
 * than only in the store.
 *
 * A REFUSAL IS RENDERED, NEVER THROWN. `Refused` carries the server's own
 * sentence — "no LLM pin is fitted on this deployment", "profile the sample
 * first" — and those are answers a BA can act on. Replacing them with a
 * generic failure would throw away the one useful thing the server said.
 */

type Proposal = { proposal_id: string };

/** Where a refusal is shown: back on the profile, with the reason in the URL. */
function back(profileId: string, message: string): never {
  redirect(`/data/intake/profile/${encodeURIComponent(profileId)}?refused=${encodeURIComponent(message)}`);
}

async function propose(
  formData: FormData,
  path: (feedId: string) => string,
  body: (profileId: string) => Record<string, unknown>,
): Promise<never> {
  const feedId = String(formData.get("feed_id") ?? "").trim();
  const profileId = String(formData.get("profile_id") ?? "").trim();
  if (!feedId || !profileId) back(profileId, "Nothing to run this against.");

  let proposal: Proposal;
  try {
    proposal = await api<Proposal>(path(feedId), {
      method: "POST",
      body: JSON.stringify(body(profileId)),
    });
  } catch (error) {
    if (error instanceof Refused) back(profileId, error.detail);
    throw error;
  }
  // `redirect` throws — it must be outside the try above, or the catch
  // written to render a refusal catches NEXT_REDIRECT and reports the
  // success as a failure. `ui/app/data/intake/deliver/actions.ts` records the
  // day that happened to every upload in the product.
  revalidatePath(`/data/intake/profile/${profileId}`);
  redirect(`/data/intake/proposals/${encodeURIComponent(proposal.proposal_id)}`);
}

/** CF-V1-E5-02 — profile in, proposed contract out, every field with a confidence. */
export async function inferSchema(formData: FormData): Promise<void> {
  await propose(
    formData,
    (feedId) => `/api/feeds/${encodeURIComponent(feedId)}/infer-schema`,
    (profileId) => ({ profile_id: profileId }),
  );
}

/** CF-V1-E5-03 — which fields are PHI, and which carry healthcare code sets. */
export async function detectPhi(formData: FormData): Promise<void> {
  await propose(
    formData,
    (feedId) => `/api/feeds/${encodeURIComponent(feedId)}/detect-phi`,
    (profileId) => ({ profile_id: profileId }),
  );
}

/** CF-V1-E6-02 — source-to-target mappings, with confidence and precedents. */
export async function suggestMapping(formData: FormData): Promise<void> {
  await propose(
    formData,
    (feedId) => `/api/feeds/${encodeURIComponent(feedId)}/suggest-mapping`,
    () => ({}),
  );
}
