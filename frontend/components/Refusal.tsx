import type { Refused } from "@/lib/api";

/**
 * A refusal, rendered as an answer.
 *
 * "You may not do that" is information, not a crash. A 404 here is the same
 * sentence the server gives for a feed that does not exist — deliberately, so
 * the UI cannot become the oracle the API refused to be.
 */
export function RefusalNotice({ refusal }: { refusal: Refused }) {
  const heading =
    refusal.status === 401
      ? "Sign in to continue"
      : refusal.status === 403
        ? "Not permitted"
        : refusal.status === 404
          ? "Not found"
          : "That did not work";
  return (
    <div className="refusal">
      <strong>{heading}</strong>
      <div className="note">{refusal.detail}</div>
    </div>
  );
}
