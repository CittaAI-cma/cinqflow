import Link from "next/link";
import { CitationChip } from "@/components/Cited";

/**
 * CF-V1-E3-05 — what the platform decided about a file somebody just sent.
 *
 * THE HEADLINE IS THE LANDING DECISION, NOT "UPLOAD SUCCEEDED". Those are
 * different facts and the second one is nearly worthless: the bytes almost
 * always arrive. What a BA needs is whether the platform accepted them, and if
 * not, WHICH NAMED CHECK said no — the same reason `LandingDecision` refuses to
 * carry a rejection without one.
 *
 * Four of the five words below come from the platform. `NOT SENT` is the
 * form's own, and it is deliberately NOT one of the four: a field nobody
 * filled in never reached the registry, and dressing it as a REJECTED delivery
 * would put a decision in the person's head that no row anywhere records.
 */

export interface Landed {
  outcome: string;
  headline: string;
  next?: string;
  cite?: string;
  profile?: string;
  key?: string;
  feedId?: string;
}

/** Reuses the status lexicon's hues rather than inventing a fifth palette. */
const SAYS: Record<string, string> = {
  ACCEPTED: "Registered, and profiled. The next four steps read this file.",
  UNEXPECTED: "Registered and parked. Nothing was discarded.",
  REJECTED: "Registered, and moved to rejected. A named check declined it.",
  SKIPPED: "The platform already holds these exact bytes.",
  REFUSED: "The platform refused the request. Nothing landed.",
  "NOT SENT": "Nothing left the browser, so nothing was decided.",
};

export function DeliveryOutcome({ landed }: { landed: Landed }) {
  const known = landed.outcome in SAYS ? landed.outcome : "REFUSED";
  return (
    <div className="card outcome" data-outcome={known}>
      <strong className="outcome-word">{landed.outcome}</strong>
      <p className="note">{SAYS[known]}</p>
      <p>{landed.headline}</p>

      {landed.key ? (
        <dl className="kv">
          <dt>Where it is</dt>
          <dd className="mono">{landed.key}</dd>
        </dl>
      ) : null}

      {landed.next ? <p className="note">{landed.next}</p> : null}

      <p className="inline">
        {landed.cite ? <CitationChip citationId={landed.cite} /> : null}
        {/* The profile is the whole point of uploading first: the schema is
            inferred from it, the mapping is checked against it, the rules are
            tested on it — and every fact on it is arithmetic, computed by
            CF-V1-E5-01's profiler with NO MODEL CALLED. */}
        {landed.profile ? (
          <Link className="cited" href={`/data/intake/profile/${landed.profile}`}>
            What the platform read from it →
          </Link>
        ) : null}
        {landed.feedId ? (
          <Link className="cited" href={`/data/intake/feed/${landed.feedId}`}>
            Back to {landed.feedId} →
          </Link>
        ) : null}
      </p>
    </div>
  );
}

/** The redirect's query, read back. One place, so both pages agree. */
export function landedFrom(
  result: Record<string, string | undefined>,
  fallbackFeedId?: string,
): Landed | null {
  if (!result.outcome) return null;
  return {
    outcome: result.outcome,
    headline: result.headline ?? "",
    next: result.next,
    cite: result.cite,
    profile: result.profile,
    key: result.key,
    feedId: result.feed ?? fallbackFeedId,
  };
}
