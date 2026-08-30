import type { Tone } from "@/components/Tag";
import type { CertificationCheck } from "@/lib/types";

/**
 * The four verdicts and six checks' own colouring, shared by the overview
 * and the per-batch page so the two screens cannot disagree about what
 * "Certified-with-Waiver" looks like.
 *
 * `Certified-with-Waiver` is deliberately NOT `"good"` — `core/certification`
 * keeps it a distinct verdict rather than a footnote on `CERTIFIED` so a
 * payer sees at a glance that something was ACCEPTED rather than PASSED, and
 * folding its colour into the clean-pass green would undo that in the one
 * place a person actually looks.
 */
export function verdictTone(verdict: string): Tone {
  switch (verdict) {
    case "Certified":
      return "good";
    case "Certified-with-Waiver":
      return "pending";
    case "Not Certified":
      return "bad";
    default:
      return "neutral"; // Pending
  }
}

/** `completed: false` is PENDING regardless of `passed` — silence is not a
 *  pass, and `Check.line()` on the Python side draws the same distinction. */
export function checkMark(check: CertificationCheck): "PASS" | "FAIL" | "PENDING" {
  if (!check.completed) return "PENDING";
  return check.passed ? "PASS" : "FAIL";
}

export function checkTone(check: CertificationCheck): Tone {
  const mark = checkMark(check);
  return mark === "PASS" ? "good" : mark === "FAIL" ? "bad" : "pending";
}
