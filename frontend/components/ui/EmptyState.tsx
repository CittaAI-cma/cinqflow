/** EmptyState — "nothing here" is a state with a cause and a next step, not a
 *  blank area of the page.
 *
 *  The rule this encodes: an empty region must say what would put something in
 *  it. "No rows were refused" is a *result* worth confirming, and reads
 *  differently from "no preview has been run yet", which is a *prompt*. Both
 *  are better than a section that silently isn't rendered, which leaves the
 *  analyst unsure whether the platform checked and found nothing or never
 *  checked at all — a distinction that matters when the answer is going to be
 *  attached to an approval.
 *
 *  Server-safe: no hooks, so a server component can render it directly.
 */

export default function EmptyState({
  /** What is absent, in the user's terms. One short line. */
  title,
  /** Why it is absent and what changes it. */
  detail,
  /** The action that would resolve it, when there is one. */
  action,
  /** `result` = we looked and there is legitimately nothing (a good outcome).
   *  `prompt`  = something has not happened yet and the user can cause it. */
  tone = "prompt",
  compact = false,
}: {
  title: React.ReactNode;
  detail?: React.ReactNode;
  action?: React.ReactNode;
  tone?: "prompt" | "result";
  compact?: boolean;
}) {
  return (
    <div className={`empty-state empty-state-${tone}${compact ? " compact" : ""}`}>
      <p className="empty-state-title">{title}</p>
      {detail ? <p className="empty-state-detail">{detail}</p> : null}
      {action ? <div className="empty-state-action">{action}</div> : null}
    </div>
  );
}
