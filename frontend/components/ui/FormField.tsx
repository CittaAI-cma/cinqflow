/** Label, required marker, control, and either a hint or an error. When a
 *  control has no backing on this build, `unavailable` states the reason in the
 *  hint slot rather than leaving a dead input unexplained. */
export default function FormField({
  label,
  htmlFor,
  required,
  hint,
  error,
  unavailable,
  span,
  children,
}: {
  label: string;
  htmlFor?: string;
  required?: boolean;
  hint?: string;
  error?: string;
  unavailable?: string;
  /** Set to span both columns of the form grid. */
  span?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className={`field${span ? " span" : ""}${unavailable ? " unavailable" : ""}`}>
      <label className="field-label" htmlFor={htmlFor}>
        {label}
        {required ? <span className="req" aria-hidden="true"> *</span> : null}
      </label>
      {children}
      {error ? (
        <p className="field-error">{error}</p>
      ) : unavailable ? (
        <p className="field-hint">{unavailable}</p>
      ) : hint ? (
        <p className="field-hint">{hint}</p>
      ) : null}
    </div>
  );
}
