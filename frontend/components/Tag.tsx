/**
 * A one-word verdict, coloured by what it MEANS rather than what it says.
 *
 * Distinct from `Status` on purpose: `Status` is the seven status words and
 * only those seven, checked against `STATUS_WORDS` so a dialect cannot spread
 * onto a rendered surface. Incident states, KNOWN/NOVEL and a certification
 * verdict are three OTHER closed vocabularies (`fingerprint.IncidentState`,
 * a boolean, `certification.Verdict`) — each real, each worth colouring, and
 * none of them one of the seven. Reusing `Status` for them would print
 * "Certified" in `.uncited` styling, which reads as a defect rather than the
 * happy path it is.
 *
 * Reuses the SAME four hues the seven status words and `.outcome` already
 * carry, rather than inventing a second palette: green means done, amber
 * means an exception was accepted or something is mid-flight, red means an
 * issue needs a person, slate means neither yet.
 */
export type Tone = "good" | "bad" | "pending" | "neutral";

export function Tag({ tone, children }: { tone: Tone; children: React.ReactNode }) {
  return (
    <span className="tag" data-tone={tone}>
      {children}
    </span>
  );
}
