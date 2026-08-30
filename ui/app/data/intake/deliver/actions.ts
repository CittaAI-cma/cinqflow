"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { Refused, upload } from "@/lib/api";
import type { Delivery } from "@/lib/types";

/**
 * CF-V1-E3-05 — one delivery, from either door, through one action.
 *
 * Data Intake has a "which feed is this?" surface and every feed has its own
 * page; both post here. That is the same argument the connector pin itself
 * makes one layer down: a second upload path is a second place for the
 * business date to be formatted differently, or for a refusal to be swallowed.
 * ADR-0011's "there is no second door" is only true if nobody builds one out
 * of convenience in the browser.
 *
 * THE SERVER DECIDES. Nothing here inspects the file, matches it against the
 * feed's pattern, or forms an opinion about its size. The two checks below are
 * about the FORM — a field nobody filled in — and both of them refuse without
 * sending anything, so there is no case where the browser has judged bytes the
 * platform never saw.
 */

/**
 * `next/navigation` implements `redirect()` by THROWING, so it must be called
 * outside any try/catch that could catch it.
 *
 * This is not a style preference. The first cut of this action called it from
 * inside the `try`, and the `catch` written to render a refusal caught the
 * redirect instead: a file the platform had received, registered and parked
 * came back to the person as `REJECTED — Error: NEXT_REDIRECT`. Every delivery
 * through the only upload surface in the product reported a false rejection.
 * `tests/wave1-intake.spec.ts` fails if it ever comes back.
 */
type Landing = {
  outcome: string;
  headline: string;
  next?: string;
  cite?: string;
  profile?: string;
  key?: string;
};

/**
 * Where the outcome is rendered. Supplied by the form so one action serves two
 * pages — and checked here, because a `return_to` that reached `redirect()`
 * unexamined would be an open redirect wearing a hidden input.
 */
const RETURNABLE = /^\/data\/intake\/(deliver|feed\/[A-Za-z0-9._-]+\/deliver)$/;

export async function deliverFile(formData: FormData): Promise<void> {
  const feedId = String(formData.get("feed_id") ?? "").trim();
  const asked = String(formData.get("return_to") ?? "");
  const back = RETURNABLE.test(asked) ? asked : "/data/intake/deliver";

  const result = await attemptDelivery(feedId, formData);
  redirect(`${back}?${query(feedId, result)}`);
}

async function attemptDelivery(feedId: string, formData: FormData): Promise<Landing> {
  if (!feedId) {
    return {
      outcome: "NOT SENT",
      headline: "Choose which feed this file belongs to. Nothing was sent.",
    };
  }
  const file = formData.get("file");
  // A file input nobody touched arrives as an unnamed zero-byte File. That is
  // an empty FORM, not an empty file — and an empty file with a name IS sent,
  // because `_size_bounds` in `core.landing` is the thing that decides that,
  // and its refusal is registered where a browser's would not be.
  if (!(file instanceof File) || file.name === "") {
    return { outcome: "NOT SENT", headline: "Choose a file first. Nothing was sent." };
  }

  const body = new FormData();
  body.set("file", file);
  body.set("business_date", String(formData.get("business_date") ?? ""));
  for (const optional of ["checksum", "declared_row_count"]) {
    const value = String(formData.get(optional) ?? "").trim();
    if (value) body.set(optional, value);
  }

  try {
    const delivery = await upload<Delivery>(
      `/api/feeds/${encodeURIComponent(feedId)}/deliveries`,
      body,
    );
    revalidatePath(`/data/intake/feed/${feedId}`);
    revalidatePath("/data/intake");
    return {
      outcome: delivery.outcome,
      headline: delivery.headline,
      next: delivery.next_step,
      cite: delivery.citation_id,
      profile: delivery.profile_id ?? undefined,
      key: delivery.landed_key || delivery.key,
    };
  } catch (error) {
    // A refusal is a DECISION the server made and recorded — it is rendered.
    // Anything else is a transport failure that reached nobody, and it is
    // re-thrown to the error boundary rather than dressed up as a decision the
    // platform did not make.
    if (!(error instanceof Refused)) throw error;
    return { outcome: "REFUSED", headline: error.detail };
  }
}

function query(feedId: string, result: Landing): string {
  const parameters = new URLSearchParams({ outcome: result.outcome, headline: result.headline });
  if (feedId) parameters.set("feed", feedId);
  for (const [key, value] of Object.entries(result)) {
    if (key === "outcome" || key === "headline" || !value) continue;
    parameters.set(key, String(value));
  }
  return parameters.toString();
}
