/** Confidence, banded.
 *
 *  A bare 0.00–1.00 bar treats 0.31 and 0.97 as the same kind of statement:
 *  both render as "a blue bar, some width". The number an analyst actually
 *  needs is not the value, it is which of three decisions it implies —
 *  take it, look at it, or don't trust it without checking. So the value is
 *  still shown exactly (it is evidence, and rounding evidence is its own
 *  problem) but it is banded, and the band is what carries visually.
 *
 *  Colour alone does not encode the band: this platform already treats
 *  colour-blind and greyscale reading as a requirement (see `StatusWord`'s
 *  shape glyphs), so the meter is segmented — one, two or three filled
 *  blocks — and the band survives being printed in black and white.
 *
 *  Thresholds are deliberately conservative. `low` starts below 0.60 because
 *  the mapping engine's own weakest deterministic signal (a lexical
 *  fallback match) is capped at 0.40, and a model-authored claim that scores
 *  under 0.60 is in the same territory: a lead, not an answer.
 */

export type ConfidenceBand = "low" | "medium" | "high";

export function bandOf(value: number): ConfidenceBand {
  if (value >= 0.85) return "high";
  if (value >= 0.6) return "medium";
  return "low";
}

const BAND_MEANING: Record<ConfidenceBand, string> = {
  high: "strong evidence",
  medium: "plausible — worth a look",
  low: "weak — verify before relying on it",
};

const FILLED: Record<ConfidenceBand, number> = { low: 1, medium: 2, high: 3 };

export default function Confidence({
  value,
  /** Shows the band's meaning inline. Off in dense tables, on in review cards. */
  withLabel = false,
}: {
  value: number;
  withLabel?: boolean;
}) {
  const clamped = Math.min(Math.max(value, 0), 1);
  const band = bandOf(clamped);
  const filled = FILLED[band];

  return (
    <span className={`confidence confidence-${band}`}>
      <span
        className="confidence-meter"
        role="meter"
        aria-valuenow={Number(clamped.toFixed(2))}
        aria-valuemin={0}
        aria-valuemax={1}
        aria-valuetext={`${clamped.toFixed(2)} — ${BAND_MEANING[band]}`}
        title={BAND_MEANING[band]}
      >
        {[0, 1, 2].map((index) => (
          <i key={index} className={index < filled ? "on" : undefined} />
        ))}
      </span>
      <span className="confidence-value" aria-hidden="true">
        {clamped.toFixed(2)}
      </span>
      {withLabel ? <span className="confidence-band">{band}</span> : null}
    </span>
  );
}
