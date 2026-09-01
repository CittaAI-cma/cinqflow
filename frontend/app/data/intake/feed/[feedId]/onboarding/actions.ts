"use server";

import { revalidatePath } from "next/cache";
import { Refused, api } from "@/lib/api";

/**
 * CF-V1-E4-03 — submit the completed onboarding for review.
 *
 * The two refusals this can return are the story's own, and BOTH are the
 * server's to make:
 *
 *   · the checklist is not green — `refuse_unready`;
 *   · the configuration changed after the end-to-end test — `refuse_stale_
 *     evidence`, which compares the pack's fingerprint against the
 *     configuration as it is NOW.
 *
 * Neither is pre-checked here. A client that decided for itself whether the
 * evidence was stale would be recomputing a fingerprint in a browser, and the
 * first time the two disagreed the person would see a green button and a 409.
 * The button is disabled when the server already said the feed is not
 * publishable, which is a courtesy; the refusal is the mechanism.
 */
export async function submitOnboarding(
  _previous: { message: string; refused: boolean } | null,
  formData: FormData,
): Promise<{ message: string; refused: boolean }> {
  const feedId = String(formData.get("feed_id") ?? "").trim();
  if (!feedId) return { message: "No feed to submit.", refused: true };

  const path = `/data/intake/feed/${encodeURIComponent(feedId)}/onboarding`;
  try {
    await api(`/api/feeds/${encodeURIComponent(feedId)}/onboarding/submit`, { method: "POST" });
  } catch (error) {
    if (error instanceof Refused) {
      // The server's own sentence, verbatim. Rewriting it here would produce
      // a second vocabulary for the same refusal — and the server's version
      // is the one that names which mapping went stale.
      return { message: error.detail, refused: true };
    }
    throw error;
  }
  revalidatePath(path);
  return {
    message: "Submitted for business and technical approval. Both approvers see the evidence pack.",
    refused: false,
  };
}


/**
 * CF-V1-E4-02 — the one button, from the browser.
 *
 * `POST /api/feeds/{id}/evidence` runs the DRAFT configuration over the
 * profiled sample through the real engine and stores the pack. Until that
 * route existed the wizard had a step 5 nobody could reach: the submit gate
 * refuses without a pack, `GET /evidence` could only read one, and nothing
 * anywhere could produce one.
 *
 * A FAILED RUN IS A SUCCESS FOR THIS ACTION. The route answers 200 with a
 * pack whose `failure` is populated — "the pack is still produced up to the
 * failure" — so a mapping type error refreshes the page and shows the reason
 * beside the rows that did map. Only a 4xx (no sample, no contract) is
 * reported as a refusal here.
 */
export async function runSampleTest(
  _previous: { message: string; refused: boolean } | null,
  formData: FormData,
): Promise<{ message: string; refused: boolean }> {
  const feedId = String(formData.get("feed_id") ?? "").trim();
  if (!feedId) return { message: "No feed to test.", refused: true };

  const path = `/data/intake/feed/${encodeURIComponent(feedId)}/onboarding`;
  type Pack = {
    rows_in: number;
    rows_loaded: number;
    rows_quarantined: number;
    failure: unknown;
  };
  let pack: Pack;
  try {
    pack = await api<Pack>(`/api/feeds/${encodeURIComponent(feedId)}/evidence`, {
      method: "POST",
      body: JSON.stringify({}),
    });
  } catch (error) {
    if (error instanceof Refused) return { message: error.detail, refused: true };
    throw error;
  }
  revalidatePath(path);
  return {
    message: pack.failure
      ? `The run stopped early — the pack below explains where. ${pack.rows_in} rows read.`
      : `${pack.rows_in} in / ${pack.rows_loaded} loaded / ${pack.rows_quarantined} quarantined. The pack is below.`,
    refused: false,
  };
}
