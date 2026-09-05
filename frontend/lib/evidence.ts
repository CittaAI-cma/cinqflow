/** How strong a piece of evidence is, by its own prefix.
 *
 *  Evidence strings are free text, but two prefixes carry real meaning worth a
 *  reader noticing at a glance:
 *
 *    `precedent:` a governed, human-approved decision applied deterministically
 *                 by `_apply_precedent_hints` — the strongest thing a field can
 *                 carry, and not a model's opinion at all.
 *    `semantic:`  an unverified lexical-similarity lead, surfaced only where
 *                 nothing structured could place the column, and capped at
 *                 `SEMANTIC_CONFIDENCE_CAP` (0.40) regardless of raw score —
 *                 worth attention, never itself a decision.
 *
 *  Everything else renders plain.
 *
 *  This lived only in `ProposalTable`, so the trust ladder was drawn on the
 *  read-only proposal view and flattened to one undifferentiated teal on the
 *  Mapping Studio — the one screen where an analyst actually decides. Shared
 *  here so the two cannot drift, and so "a human already approved this" and
 *  "two names look a bit alike" stop looking equally strong at the moment of
 *  the decision.
 */
export function evidenceClass(item: string): string {
  if (item.startsWith("precedent:")) return "evidence-chip--precedent";
  if (item.startsWith("semantic:")) return "evidence-chip--semantic";
  return "";
}
