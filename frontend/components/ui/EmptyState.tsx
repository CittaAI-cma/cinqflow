/**
 * Nothing here — and WHICH nothing.
 *
 * The old `<div className="card note">` collapsed three different facts into
 * one grey sentence. They are not the same fact and a user must not have to
 * guess which one they are looking at:
 *
 *   · `recorded`  — the platform looked and there is nothing. A fact, not a gap.
 *   · `scope`     — it may exist; you cannot see it. Deliberately identical to
 *                   "does not exist", because a distinguishable refusal tells
 *                   the caller the object is real (core/security, scope_miss).
 *   · `wave`      — this is not built yet. Never a stub, never a fake screen.
 */
export function EmptyState({
  kind,
  what,
  action,
}: {
  kind: "recorded" | "scope" | "wave";
  /** The thing that is absent, in the platform's own words. */
  what: string;
  action?: React.ReactNode;
}) {
  const copy = {
    recorded: {
      title: `Nothing recorded for ${what} yet.`,
      body: "That is a fact, not a gap to fill in.",
    },
    scope: {
      title: "Nothing to show.",
      body:
        "This is either not recorded, or not something you have access to — the platform " +
        "deliberately answers both the same way.",
    },
    wave: {
      title: `${what} is not available yet.`,
      body: "It activates with its wave. There is no stub behind this, and nothing is hidden.",
    },
  }[kind];

  return (
    <div className="card empty">
      <span className="empty-title">{copy.title}</span>
      <span className="note">{copy.body}</span>
      {action && <div style={{ marginTop: "var(--s-3)" }}>{action}</div>}
    </div>
  );
}
