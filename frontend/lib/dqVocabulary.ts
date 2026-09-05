/** The data-quality action vocabulary, verbatim from `knowledge/dq/severity.yaml`
 *  (`actions`). The knowledge layer has no HTTP surface yet
 *  (knowledge-base-screen.md §5), so the Quality panel carries the legend as
 *  text. Kept in the YAML's own order, block first: the order a reader ranks
 *  them in. */
export const DQ_ACTIONS: { action: string; meaning: string }[] = [
  { action: "block", meaning: "Fails the cycle for this table. No promotion. Human notified." },
  {
    action: "quarantine",
    meaning: "Affected rows held, counted, visible, reprocessable. Cycle continues.",
  },
  {
    action: "warn",
    meaning: "Promote with a quality flag that propagates to every downstream consumer.",
  },
  {
    action: "observe",
    meaning: "Record and trend. No action this cycle; the signal is in the trajectory.",
  },
];

/** Where the platform stands today against that vocabulary: promotion applies
 *  the approved mapping's null rules, casts and value maps, and rows it cannot
 *  write go to quarantine - `quarantined`/`rejected` outcomes. `block` is the
 *  balance equation refusing to finish a run; `warn` and `observe` arrive with
 *  the rules studio (E7). Stated so the legend never overclaims. */
export const DQ_TODAY =
  "Today promotion applies the approved mapping's null rules, casts and value maps and holds what it cannot write in quarantine; a run that does not balance is refused (block). Warn and observe arrive with the rules studio.";
